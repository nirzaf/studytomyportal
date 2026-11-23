"""Policy-bound retention and removal for ungraded interactive lesson practice."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator
from sqlalchemy import exists, select
from sqlmodel import delete
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.governance import StudyBusinessEvent, StudyPolicyBody, StudyPolicyKey
from src.db.studytomy.practice import StudyLearningAction, StudyLearningSurface
from src.db.users import PublicUser, User
from src.security.studytomy_access import StudytomyCapabilityName, authorize_studytomy_staff_action
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.errors import POLICY_NOT_APPROVED, studytomy_http_error
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization

_HOLD = "studytomy.learning_practice.retention_hold_placed"
_RELEASE = "studytomy.learning_practice.retention_hold_released"
_REMOVED = "studytomy.learning_practice.retention_removed"
ReasonCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{2,63}$")]
logger = logging.getLogger(__name__)


class PracticeRetentionRules(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    retention_hours: Annotated[int, Field(ge=1)]
    retention_category: Literal["lesson_visual_practice"]
    hold_reason_codes: Annotated[list[ReasonCode], Field(min_length=1, max_length=32)]
    hold_release_reason_codes: Annotated[list[ReasonCode], Field(min_length=1, max_length=32)]
    removal_reason_codes: Annotated[list[ReasonCode], Field(min_length=1, max_length=32)]
    max_hold_review_days: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def validate_reason_sets(self):
        for values in (self.hold_reason_codes, self.hold_release_reason_codes, self.removal_reason_codes):
            if len(values) != len(set(values)):
                raise ValueError("Retention reason codes must be unique")
        return self


class PracticeRemovalRules(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    automatic_expiry_enabled: bool
    authorized_removal_enabled: bool


class PracticeRetentionHoldCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # UUIDs arrive as JSON strings over the HTTP API; validate their format while
    # keeping strict validation for the rest of the command fields.
    operation_uuid: Annotated[UUID, Field(strict=False)]
    reason_code: ReasonCode


class PracticeRetentionReleaseCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation_uuid: Annotated[UUID, Field(strict=False)]
    hold_event_uuid: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    reason_code: ReasonCode


class PracticeRetentionRemovalCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    operation_uuid: Annotated[UUID, Field(strict=False)]
    reason_code: ReasonCode


class PracticeRetentionReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface_uuid: str
    event_uuid: str
    status: str


def _retention_decision(policy) -> tuple[PracticeRetentionRules, PracticeRemovalRules]:
    body = StudyPolicyBody.model_validate(policy.body)
    retention_area = body.specified_value("retention")
    removal_area = body.specified_value("removal")
    retention = retention_area.get("lesson_visual_practice") if isinstance(retention_area, dict) else None
    removal = removal_area.get("lesson_visual_practice") if isinstance(removal_area, dict) else None
    try:
        rules = PracticeRetentionRules.model_validate(retention)
        removal_rules = PracticeRemovalRules.model_validate(removal)
    except (ValidationError, TypeError, ValueError) as error:
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "Current D-13 policy does not approve practice retention and removal.") from error
    if rules.retention_category != "lesson_visual_practice":
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "D-13 practice retention category is invalid.")
    return rules, removal_rules


async def current_practice_retention(db: AsyncSession, org: Organization, now: datetime | None = None):
    now = now or datetime.now(UTC)
    (policy,) = await _current_policies(db, org, (StudyPolicyKey.D_13,), now)
    rules, removal = _retention_decision(policy)
    return policy, rules, removal


async def _practice_surface(db: AsyncSession, org: Organization, surface_uuid: str, *, lock: bool = False):
    query = select(StudyLearningSurface).where(
        StudyLearningSurface.org_id == org.id,
        StudyLearningSurface.surface_uuid == surface_uuid,
    )
    if lock:
        query = query.with_for_update()
    surface = (await db.execute(query)).scalar_one_or_none()
    if surface is None or surface.retention_policy_id is None or surface.retention_category != "lesson_visual_practice":
        raise studytomy_http_error(404, "PRACTICE_RECORD_UNAVAILABLE", "Practice record unavailable.")
    learner = await db.get(User, surface.user_id)
    if learner is None:
        raise studytomy_http_error(404, "PRACTICE_RECORD_UNAVAILABLE", "Practice record unavailable.")
    return surface, learner


async def _authorize_retention_reviewer(db: AsyncSession, org: Organization, actor: PublicUser, learner: User, now: datetime):
    if actor.id == learner.id:
        raise studytomy_http_error(404, "PRACTICE_RECORD_UNAVAILABLE", "Practice record unavailable.")
    return await authorize_studytomy_staff_action(
        db,
        actor,
        org,
        StudytomyCapabilityName.PRIVACY_RETENTION_REVIEW,
        target_scope_kind="learner",
        target_resource_uuid=learner.user_uuid,
        subject_user_id=learner.id,
        hide_existence_on_denial=True,
        now=now,
    )


async def _active_holds(db: AsyncSession, org_id: int, surface_uuid: str) -> list[StudyBusinessEvent]:
    holds = (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org_id,
        StudyBusinessEvent.resource_kind == "learning_practice_surface",
        StudyBusinessEvent.resource_uuid == surface_uuid,
        StudyBusinessEvent.action == _HOLD,
    ).order_by(StudyBusinessEvent.id))).scalars().all()
    releases = (await db.execute(select(StudyBusinessEvent.operation_uuid).where(
        StudyBusinessEvent.org_id == org_id,
        StudyBusinessEvent.resource_kind == "learning_practice_surface",
        StudyBusinessEvent.resource_uuid == surface_uuid,
        StudyBusinessEvent.action == _RELEASE,
    ))).scalars().all()
    released = set(releases)
    return [event for event in holds if event.event_uuid not in released]


async def place_practice_retention_hold(
    db: AsyncSession, org: Organization, actor: PublicUser, surface_uuid: str,
    command: PracticeRetentionHoldCommand,
) -> PracticeRetentionReceipt:
    now = datetime.now(UTC)
    await lock_organization(db, org.id)
    surface, learner = await _practice_surface(db, org, surface_uuid, lock=True)
    authority = await _authorize_retention_reviewer(db, org, actor, learner, now)
    policy, rules, _ = await current_practice_retention(db, org, now)
    if command.reason_code not in rules.hold_reason_codes:
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "D-13 does not approve this practice hold reason.")
    dedupe_key = f"studytomy:practice:hold:{org.id}:{surface_uuid}:{command.operation_uuid}"
    replay = await db.scalar(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.dedupe_key == dedupe_key,
    ))
    if replay is not None:
        if replay.actor_user_id != actor.id or replay.reason != command.reason_code:
            raise studytomy_http_error(409, "IDEMPOTENCY_CONFLICT", "The hold request ID has different content.")
        return PracticeRetentionReceipt(surface_uuid=surface_uuid, event_uuid=replay.event_uuid, status="held")
    active = await _active_holds(db, org.id, surface_uuid)
    if active:
        raise studytomy_http_error(409, "PRACTICE_RETENTION_ALREADY_HELD", "Practice record already has an active hold.")
    event = await append_business_event(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        actor_capacity="privacy_reviewer",
        subject_uuid=learner.user_uuid,
        resource_kind="learning_practice_surface",
        resource_uuid=surface_uuid,
        action=_HOLD,
        reason_code=command.reason_code,
        policy_refs=sorted(set(authority.policy_version_ids + [int(policy.id), surface.retention_policy_id])),
        after_state={
            "policy_version_id": int(policy.id),
            "review_due_at": _policy_due_date(now, rules.max_hold_review_days).isoformat(),
        },
        operation_uuid=str(command.operation_uuid),
        dedupe_key=dedupe_key,
    )
    await db.commit()
    return PracticeRetentionReceipt(surface_uuid=surface_uuid, event_uuid=event.event_uuid, status="held")


async def release_practice_retention_hold(
    db: AsyncSession, org: Organization, actor: PublicUser, surface_uuid: str,
    command: PracticeRetentionReleaseCommand,
) -> PracticeRetentionReceipt:
    now = datetime.now(UTC)
    await lock_organization(db, org.id)
    surface, learner = await _practice_surface(db, org, surface_uuid, lock=True)
    authority = await _authorize_retention_reviewer(db, org, actor, learner, now)
    policy, rules, _ = await current_practice_retention(db, org, now)
    if command.reason_code not in rules.hold_release_reason_codes:
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "D-13 does not approve this practice hold release reason.")
    hold = await db.scalar(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id,
        StudyBusinessEvent.event_uuid == command.hold_event_uuid,
        StudyBusinessEvent.resource_kind == "learning_practice_surface",
        StudyBusinessEvent.resource_uuid == surface_uuid,
        StudyBusinessEvent.action == _HOLD,
    ))
    if hold is None or hold.actor_user_id == actor.id:
        raise studytomy_http_error(404, "PRACTICE_RETENTION_HOLD_UNAVAILABLE", "Practice hold unavailable.")
    release_key = f"studytomy:practice:hold-release:{org.id}:{hold.event_uuid}"
    replay = await db.scalar(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.dedupe_key == release_key,
    ))
    if replay is not None:
        if replay.actor_user_id != actor.id or replay.reason != command.reason_code:
            raise studytomy_http_error(409, "IDEMPOTENCY_CONFLICT", "The hold release already has a different decision.")
        return PracticeRetentionReceipt(surface_uuid=surface_uuid, event_uuid=replay.event_uuid, status="released")
    if hold.event_uuid not in {item.event_uuid for item in await _active_holds(db, org.id, surface_uuid)}:
        raise studytomy_http_error(409, "PRACTICE_RETENTION_HOLD_RELEASED", "Practice hold has already been released.")
    event = await append_business_event(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        actor_capacity="privacy_reviewer",
        subject_uuid=learner.user_uuid,
        resource_kind="learning_practice_surface",
        resource_uuid=surface_uuid,
        action=_RELEASE,
        reason_code=command.reason_code,
        policy_refs=sorted(set(authority.policy_version_ids + [int(policy.id), surface.retention_policy_id])),
        after_state={"policy_version_id": int(policy.id), "previous_event_uuid": hold.event_uuid},
        operation_uuid=hold.event_uuid,
        dedupe_key=release_key,
    )
    await db.commit()
    return PracticeRetentionReceipt(surface_uuid=surface_uuid, event_uuid=event.event_uuid, status="released")


async def remove_expired_practice_surface(
    db: AsyncSession,
    org: Organization,
    surface: StudyLearningSurface,
    *,
    actor: PublicUser | None = None,
    reason_code: str = "PRACTICE_RETENTION_EXPIRED",
    manual_policy_refs: list[int] | None = None,
    now: datetime | None = None,
) -> str:
    now = now or datetime.now(UTC)
    locked, learner = await _practice_surface(db, org, surface.surface_uuid, lock=True)
    policy, rules, removal = await current_practice_retention(db, org, now)
    if locked.retention_expires_at is None or locked.retention_expires_at > now:
        raise studytomy_http_error(409, "PRACTICE_RETENTION_NOT_EXPIRED", "Practice record is still within its approved retention period.")
    if actor is None and not removal.automatic_expiry_enabled:
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "D-13 does not authorize automatic practice expiry.")
    if actor is not None:
        if not removal.authorized_removal_enabled or reason_code not in rules.removal_reason_codes:
            raise studytomy_http_error(409, POLICY_NOT_APPROVED, "D-13 does not authorize this practice removal.")
        authority = await _authorize_retention_reviewer(db, org, actor, learner, now)
        policy_refs = sorted(set(authority.policy_version_ids + [int(policy.id), locked.retention_policy_id]))
        actor_user_id, actor_machine_id, capacity = actor.id, None, "privacy_reviewer"
    else:
        policy_refs = sorted(set([int(policy.id), locked.retention_policy_id]))
        if manual_policy_refs:
            policy_refs = sorted(set(policy_refs + manual_policy_refs))
        actor_user_id, actor_machine_id, capacity = None, "practice-retention-worker", "retention_worker"
    if await _active_holds(db, org.id, locked.surface_uuid):
        raise studytomy_http_error(409, "PRACTICE_RETENTION_HELD", "An active privacy hold prevents practice removal.")
    event = await append_business_event(
        db,
        org_id=org.id,
        actor_user_id=actor_user_id,
        actor_machine_id=actor_machine_id,
        actor_capacity=capacity,
        subject_uuid=learner.user_uuid,
        resource_kind="learning_practice_surface",
        resource_uuid=locked.surface_uuid,
        action=_REMOVED,
        reason_code=reason_code,
        policy_refs=policy_refs,
        before_state={
            "policy_version_id": locked.retention_policy_id,
            "retention_cutoff": locked.retention_expires_at.isoformat(),
            "source_snapshot_digest": locked.source_identity,
        },
        operation_uuid=str(uuid4()),
        dedupe_key=f"studytomy:practice:removed:{org.id}:{locked.surface_uuid}",
    )
    await db.execute(delete(StudyLearningAction).where(StudyLearningAction.surface_uuid == locked.surface_uuid))
    await db.delete(locked)
    return event.event_uuid


async def remove_practice_surface_manually(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    surface_uuid: str,
    command: PracticeRetentionRemovalCommand,
) -> PracticeRetentionReceipt:
    await lock_organization(db, org.id)
    replay = await db.scalar(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id,
        StudyBusinessEvent.resource_kind == "learning_practice_surface",
        StudyBusinessEvent.resource_uuid == surface_uuid,
        StudyBusinessEvent.action == _REMOVED,
    ))
    if replay is not None:
        if replay.actor_user_id != actor.id or replay.reason != command.reason_code:
            raise studytomy_http_error(409, "IDEMPOTENCY_CONFLICT", "This practice record already has a different removal receipt.")
        learner = await db.scalar(select(User).where(User.user_uuid == replay.subject_uuid))
        if learner is None:
            raise studytomy_http_error(404, "PRACTICE_RECORD_UNAVAILABLE", "Practice record unavailable.")
        await _authorize_retention_reviewer(db, org, actor, learner, datetime.now(UTC))
        return PracticeRetentionReceipt(surface_uuid=surface_uuid, event_uuid=replay.event_uuid, status="removed")
    surface, _ = await _practice_surface(db, org, surface_uuid, lock=True)
    event_uuid = await remove_expired_practice_surface(
        db, org, surface, actor=actor, reason_code=command.reason_code,
    )
    await db.commit()
    return PracticeRetentionReceipt(surface_uuid=surface_uuid, event_uuid=event_uuid, status="removed")


async def expire_practice_batch(db: AsyncSession, org: Organization, *, limit: int = 50) -> int:
    """Delete only policy-expired, unheld records and leave an immutable receipt."""
    now = datetime.now(UTC)
    # Match governance operations' lock order: organization first, then practice rows.
    await lock_organization(db, org.id)
    policy, _, removal = await current_practice_retention(db, org, now)
    if not removal.automatic_expiry_enabled:
        return 0
    hold_event = StudyBusinessEvent.__table__.alias("practice_expiry_hold")
    release_event = StudyBusinessEvent.__table__.alias("practice_expiry_release")
    active_hold = exists(select(1).select_from(hold_event).where(
        hold_event.c.org_id == StudyLearningSurface.org_id,
        hold_event.c.resource_kind == "learning_practice_surface",
        hold_event.c.resource_uuid == StudyLearningSurface.surface_uuid,
        hold_event.c.action == _HOLD,
        ~exists(select(1).select_from(release_event).where(
            release_event.c.org_id == hold_event.c.org_id,
            release_event.c.resource_kind == hold_event.c.resource_kind,
            release_event.c.resource_uuid == hold_event.c.resource_uuid,
            release_event.c.action == _RELEASE,
            release_event.c.operation_uuid == hold_event.c.event_uuid,
        )),
    ))
    records = (await db.execute(select(StudyLearningSurface).where(
        StudyLearningSurface.org_id == org.id,
        StudyLearningSurface.retention_category == "lesson_visual_practice",
        StudyLearningSurface.retention_policy_id.is_not(None),
        StudyLearningSurface.retention_expires_at <= now,
        ~active_hold,
    ).order_by(StudyLearningSurface.retention_expires_at, StudyLearningSurface.surface_uuid)
        .limit(limit).with_for_update(skip_locked=True))).scalars().all()
    removed = 0
    for record in records:
        try:
            await remove_expired_practice_surface(db, org, record, now=now)
            removed += 1
        except Exception as error:  # A single held or stale record must not stall the bounded batch.
            if getattr(error, "status_code", None) == 409:
                continue
            raise
    return removed


async def _retention_scheduler_loop() -> None:
    from src.core.events.database import _async_session_factory

    while True:
        await asyncio.sleep(3600)
        try:
            async with _async_session_factory() as db:
                org_ids = (await db.execute(select(StudyLearningSurface.org_id).where(
                    StudyLearningSurface.retention_category == "lesson_visual_practice",
                    StudyLearningSurface.retention_expires_at <= datetime.now(UTC),
                ).distinct().limit(100))).scalars().all()
            for org_id in org_ids:
                try:
                    async with _async_session_factory() as db:
                        async with db.begin():
                            org = await db.get(Organization, org_id)
                            if org is not None:
                                await expire_practice_batch(db, org)
                except Exception as error:
                    logger.warning("Lesson practice retention skipped one organization (%s)", type(error).__name__)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Lesson practice retention pass failed")


_retention_task: asyncio.Task | None = None


def _policy_due_date(now: datetime, days: int) -> datetime:
    try:
        return now + timedelta(days=days)
    except OverflowError as error:
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "D-13 hold review horizon is invalid.") from error


def start_scheduler() -> None:
    """Start policy-gated cleanup; no records are eligible without explicit D-13 approval."""
    global _retention_task
    if _retention_task is None or _retention_task.done():
        _retention_task = asyncio.create_task(_retention_scheduler_loop())


async def stop_scheduler() -> None:
    global _retention_task
    if _retention_task is not None:
        _retention_task.cancel()
        try:
            await _retention_task
        except asyncio.CancelledError:
            pass
        _retention_task = None
