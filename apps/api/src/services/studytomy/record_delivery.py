"""Prepare and release a reviewed projection of retained completion records."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.education import StudyCourseRelease, StudyCourseReview
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyPolicyKey,
    inspect_study_policy_body,
)
from src.db.trail_runs import StatusEnum, TrailRun
from src.db.users import PublicUser
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization
from src.services.studytomy.record_requests import (
    _ACTION,
    _record_access_rule,
    _record_request_scopes,
    _retained_decision,
    _self_profile,
    _staff_request_context,
)

_RELEASED = "studytomy.record_request.delivery_released"
_HANDED_OFF = "studytomy.record_request.delivery_handed_off"
_MAX_RECORDS = 500


class RetainedCompletion(BaseModel):
    course_uuid: str
    recorded_at: datetime
    release_uuid: str | None = Field(default=None, exclude_if=lambda value: value is None)


class CompletionExport(BaseModel):
    completions: list[RetainedCompletion]


class CompletionPreview(CompletionExport):
    snapshot_sha256: str


class CompletionReleaseCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CompletionReleaseReceipt(BaseModel):
    request_reference: UUID
    available_until: datetime


def _delivery_rules(information, retention) -> tuple[int, int]:
    rule = _record_access_rule(retention)
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_13, retention.body)
    limits = body.specified_value("retention") if body else None
    retention_days = limits.get("course_completion_days") if isinstance(limits, dict) else None
    delivery_days = rule.get("completion_delivery_days")
    if (
        "self_course_completion_delivery" not in _record_request_scopes(information)
        or type(retention_days) is not int or not 1 <= retention_days <= 36500
        or type(delivery_days) is not int or not 1 <= delivery_days <= 30
    ):
        raise studytomy_http_error(409, "RECORD_DELIVERY_NOT_APPROVED", "Current policy does not approve completion delivery.")
    return retention_days, delivery_days


async def _completion_export(db: AsyncSession, request: StudyBusinessEvent, now: datetime, days: int) -> CompletionExport:
    earliest = now - timedelta(days=days)
    rows = (await db.execute(select(Course.course_uuid, TrailRun.update_date)
        .join(TrailRun, TrailRun.course_id == Course.id)
        .where(Course.org_id == request.org_id, Course.studytomy_managed.is_(True),
               TrailRun.org_id == request.org_id, TrailRun.user_id == request.actor_user_id,
               TrailRun.status == StatusEnum.STATUS_COMPLETED)
        .order_by(Course.course_uuid, TrailRun.id).limit(_MAX_RECORDS + 1))).all()
    release_rows = (await db.execute(
        _reviewed_completion_rows(request.org_id, request.actor_user_id)
        .where(StudyBusinessEvent.created_at >= earliest, StudyBusinessEvent.created_at <= now)
        .order_by(StudyCourseRelease.course_uuid, StudyBusinessEvent.id)
        .limit(_MAX_RECORDS + 1)
    )).all()
    if len(rows) + len(release_rows) > _MAX_RECORDS:
        raise studytomy_http_error(409, "RECORD_DELIVERY_REVIEW_REQUIRED", "The records require a separate reviewed export.")
    completions = []
    for course_uuid, recorded_at in rows:
        try:
            stamp = datetime.fromisoformat(recorded_at)
        except ValueError:
            continue
        if stamp.tzinfo is not None and earliest <= stamp <= now:
            completions.append(RetainedCompletion(course_uuid=course_uuid, recorded_at=stamp.astimezone(UTC)))
    for event, course_uuid, digest, progression_policy_id in release_rows:
        if not _matches_release_completion(event, digest, progression_policy_id):
            continue
        stamp = event.created_at
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=UTC)
        completions.append(RetainedCompletion(
            course_uuid=course_uuid, release_uuid=event.resource_uuid, recorded_at=stamp.astimezone(UTC),
        ))
    return CompletionExport(completions=completions)


def _reviewed_completion_rows(org_id: int, learner_user_id: int):
    return (
        select(
            StudyBusinessEvent, StudyCourseRelease.course_uuid, StudyCourseRelease.snapshot_sha256,
            StudyCourseRelease.progression_policy_id,
        )
        .join(StudyCourseRelease, StudyCourseRelease.uuid == StudyBusinessEvent.resource_uuid)
        .join(StudyCourseReview, StudyCourseReview.id == StudyCourseRelease.review_id)
        .where(
            StudyBusinessEvent.org_id == org_id,
            StudyBusinessEvent.actor_user_id == learner_user_id,
            StudyBusinessEvent.resource_kind == "course_release",
            StudyBusinessEvent.action == "studytomy.course.completion_recorded",
            StudyCourseRelease.org_id == org_id,
            StudyCourseRelease.policy_binding.is_not(None),
            StudyCourseReview.org_id == org_id,
            StudyCourseReview.course_uuid == StudyCourseRelease.course_uuid,
            StudyCourseReview.decision == "approved",
            StudyCourseReview.revision_sha256 == StudyCourseRelease.snapshot_sha256,
        )
    )


def _matches_release_completion(event: StudyBusinessEvent, digest: str, progression_policy_id: int) -> bool:
    return event.policy_refs == [progression_policy_id] and event.after_state == {"source_snapshot_digest": digest}


async def has_reviewed_completion_before_window(
    db: AsyncSession, org_id: int, learner_user_id: int, course_uuid: str,
    earliest: datetime, starts_at: datetime, now: datetime,
) -> bool:
    rows = (await db.execute(_reviewed_completion_rows(org_id, learner_user_id).where(
        StudyCourseRelease.course_uuid == course_uuid,
        StudyBusinessEvent.created_at >= earliest,
        StudyBusinessEvent.created_at < starts_at,
        StudyBusinessEvent.created_at <= now,
    ).limit(_MAX_RECORDS + 1))).all()
    return len(rows) <= _MAX_RECORDS and any(
        _matches_release_completion(event, digest, policy_id) for event, _, digest, policy_id in rows
    )


async def _prepared_request(db, org, actor, reference):
    request, policies, authority_refs = await _staff_request_context(db, org, actor, reference)
    authorized_profile = (await db.execute(select(StudyLearnerProfile.id).where(
        StudyLearnerProfile.org_id == org.id, StudyLearnerProfile.learner_uuid == request.subject_uuid,
        StudyLearnerProfile.user_id == request.actor_user_id, StudyLearnerProfile.control_mode == "independent",
    ))).scalar_one_or_none()
    if authorized_profile is None:
        raise studytomy_http_error(404, "RECORD_DELIVERY_UNAVAILABLE", "Record delivery unavailable.")
    policy_ids = sorted({policy.id for policy in policies} | set(authority_refs))
    decision = await _retained_decision(db, org.id, f"studytomy:record_request:decision:{org.id}:{reference}")
    if (
        decision is None or decision.operation_uuid != request.event_uuid
        or (decision.after_state or {}).get("status") != "approved_for_preparation"
        or not (decision.after_state or {}).get("evidence_sha256")
        or decision.policy_refs != policy_ids
    ):
        raise studytomy_http_error(409, "RECORD_DELIVERY_REVIEW_REQUIRED", "A verified preparation decision is required.")
    return request, policies, policy_ids


async def preview_completion_export(db, org, actor, reference: UUID) -> CompletionPreview:
    request, policies, _ = await _prepared_request(db, org, actor, reference)
    days, _ = _delivery_rules(*policies[1:])
    payload = await _completion_export(db, request, datetime.now(UTC), days)
    return CompletionPreview(completions=payload.completions, snapshot_sha256=canonical_request_hash(payload))


async def release_completion_export(
    db: AsyncSession, org: Organization, actor: PublicUser, reference: UUID, command: CompletionReleaseCommand,
) -> CompletionReleaseReceipt:
    org = await lock_organization(db, org.id)
    request, policies, policy_ids = await _prepared_request(db, org, actor, reference)
    days, delivery_days = _delivery_rules(*policies[1:])
    now = datetime.now(UTC)
    existing = await _delivery_event(db, request)
    if existing is not None:
        if (existing.after_state or {}).get("source_snapshot_digest") != command.snapshot_sha256:
            raise studytomy_http_error(409, "RECORD_DELIVERY_ALREADY_RELEASED", "A different snapshot was released.")
        return CompletionReleaseReceipt(
            request_reference=reference, available_until=datetime.fromisoformat(existing.after_state["valid_to"]),
        )
    payload = await _completion_export(db, request, now, days)
    if canonical_request_hash(payload) != command.snapshot_sha256:
        raise studytomy_http_error(409, "RECORD_DELIVERY_SNAPSHOT_CHANGED", "Review the current snapshot before release.")
    expires = now + timedelta(days=delivery_days)
    await append_business_event(
        db, org_id=org.id, actor_user_id=actor.id, action=_RELEASED, reason_code="COMPLETION_EXPORT_REVIEWED",
        subject_uuid=request.subject_uuid, resource_kind="record_request", resource_uuid=str(reference),
        operation_uuid=request.event_uuid, dedupe_key=f"studytomy:record_request:delivery:{org.id}:{reference}",
        policy_refs=policy_ids,
        after_state={"source_snapshot_digest": command.snapshot_sha256, "valid_to": expires.isoformat()},
    )
    return CompletionReleaseReceipt(request_reference=reference, available_until=expires)


async def _delivery_event(db: AsyncSession, request: StudyBusinessEvent) -> StudyBusinessEvent | None:
    return (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == request.org_id, StudyBusinessEvent.action == _RELEASED,
        StudyBusinessEvent.operation_uuid == request.event_uuid,
        StudyBusinessEvent.dedupe_key == f"studytomy:record_request:delivery:{request.org_id}:{request.resource_uuid}",
    ))).scalar_one_or_none()


async def hand_off_my_completion_export(
    db: AsyncSession, org: Organization, actor: PublicUser, reference: UUID,
) -> tuple[CompletionExport, str]:
    org = await lock_organization(db, org.id)
    profile = await _self_profile(db, org, actor)
    request = (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.action == _ACTION,
        StudyBusinessEvent.actor_user_id == actor.id, StudyBusinessEvent.subject_uuid == profile.learner_uuid,
        StudyBusinessEvent.dedupe_key == f"studytomy:record_request:{org.id}:{reference}",
    ))).scalar_one_or_none()
    delivery = await _delivery_event(db, request) if request else None
    if delivery is None:
        raise studytomy_http_error(404, "RECORD_DELIVERY_UNAVAILABLE", "Record delivery unavailable.")
    now = datetime.now(UTC)
    policies = await _current_policies(
        db, org, (StudyPolicyKey.D_03, StudyPolicyKey.D_12, StudyPolicyKey.D_13, StudyPolicyKey.D_15), now,
    )
    days, _ = _delivery_rules(*policies[1:3])
    state = delivery.after_state or {}
    if sorted(policy.id for policy in policies) != delivery.policy_refs or datetime.fromisoformat(state["valid_to"]) <= now:
        raise studytomy_http_error(404, "RECORD_DELIVERY_UNAVAILABLE", "Record delivery unavailable.")
    payload = await _completion_export(db, request, now, days)
    if canonical_request_hash(payload) != state.get("source_snapshot_digest"):
        raise studytomy_http_error(409, "RECORD_DELIVERY_SNAPSHOT_CHANGED", "The retained records need a new review.")
    key = f"studytomy:record_request:handoff:{org.id}:{delivery.event_uuid}"
    receipt = (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.dedupe_key == key,
        StudyBusinessEvent.action == _HANDED_OFF, StudyBusinessEvent.actor_user_id == actor.id,
    ))).scalar_one_or_none()
    if receipt is None:
        receipt = await append_business_event(
            db, org_id=org.id, actor_user_id=actor.id, action=_HANDED_OFF,
            reason_code="COMPLETION_EXPORT_HANDED_OFF", subject_uuid=profile.learner_uuid,
            resource_kind="record_request", resource_uuid=str(reference), operation_uuid=delivery.event_uuid,
            policy_refs=delivery.policy_refs, dedupe_key=key,
            after_state={"source_snapshot_digest": state["source_snapshot_digest"]},
        )
    return payload, receipt.event_uuid
