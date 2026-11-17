"""Authenticated diagnostic rounds and atomic public response receipts."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter
from sqlalchemy import case, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.adaptive import StudyConcept, StudyConceptActivity
from src.db.studytomy.adaptive_diagnostics import StudyDiagnosticRevision
from src.db.studytomy.adaptive_evidence import (
    StudyAttemptItem,
    StudyLearningAttempt,
    StudyLearningEvidence,
)
from src.db.studytomy.adaptive_items import StudyItemRevision
from src.db.studytomy.governance import StudyBusinessEvent
from src.db.users import PublicUser
from src.services.studytomy.adaptive_attempt_access import (
    DIAGNOSTIC_SCOPE,
    PRACTICE_SCOPE,
    DiagnosticAccess,
    lock_attempt_learner,
    require_diagnostic_access,
)
from src.services.studytomy.adaptive_curriculum import Version
from src.services.studytomy.adaptive_diagnostic_selection import (
    DiagnosticObservation,
    select_diagnostic_item,
)
from src.services.studytomy.adaptive_evidence import append_objective_evidence
from src.services.studytomy.adaptive_item_source import PublicQuestion
from src.services.studytomy.adaptive_mastery import _utc
from src.services.studytomy.adaptive_objective_scoring import (
    ChoiceResponse,
    InvalidObjectiveResponse,
    NumberResponse,
    evaluate_objective_item,
)
from src.services.studytomy.adaptive_practice import (
    PracticeAccess,
    require_practice_access,
)
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.errors import (
    POLICY_NOT_APPROVED,
    STALE_VERSION,
    studytomy_http_error,
)
from src.services.studytomy.idempotency import (
    canonical_request_hash,
    claim_idempotency,
    complete_idempotency,
)
from src.services.studytomy.policies import lock_organization

_QUESTION = TypeAdapter(PublicQuestion)
_START = "studytomy.adaptive.diagnostic_attempt.started"
_PRACTICE_START = "studytomy.adaptive.practice_attempt.started"
_RESPOND = "studytomy.adaptive.diagnostic_attempt.responded"
_ABANDON = "studytomy.adaptive.diagnostic_attempt.abandoned"
_CLOSE = "studytomy.adaptive.diagnostic_attempt.closed"
AttemptStatus = Literal["active", "completed", "abandoned", "expired", "invalidated"]
StopReason = Literal[
    "coverage_met",
    "item_limit",
    "insufficient_items",
    "abandoned",
    "expired",
    "invalidated",
    "practice_completed",
]


class DiagnosticStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diagnostic_revision_uuid: Annotated[
        str, StringConstraints(pattern=r"^diagnostic_[0-9a-f-]{36}$")
    ]
    source_activity_uuid: Annotated[str, StringConstraints(min_length=1, max_length=89)]


class PracticeStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept_uuid: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    source_activity_uuid: Annotated[str, StringConstraints(min_length=1, max_length=89)]
    mode: Literal["supported"]


class DiagnosticAbandon(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Version
    reason: Literal["leave_assessment", "restart_later"]


class SkipResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["skip"]


class DiagnosticResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    item_uuid: Annotated[str, StringConstraints(pattern=r"^issued_[0-9a-f-]{36}$")]
    expected_version: Version
    response: Annotated[
        ChoiceResponse | NumberResponse | SkipResponse, Field(discriminator="kind")
    ]
    declared_external_help: bool


class IssuedQuestion(BaseModel):
    uuid: str
    ordinal: int
    question: PublicQuestion
    allowed_actions: tuple[Literal["answer", "skip"], ...] = ("answer", "skip")


class AttemptRead(BaseModel):
    uuid: str
    purpose: Literal["diagnostic", "practice"] = "diagnostic"
    status: AttemptStatus
    version: int
    curriculum_uuid: str
    diagnostic_revision_uuid: str | None
    started_at: datetime
    expires_at: datetime
    answered_count: int
    skipped_count: int
    visited_concepts: int
    required_concepts: int
    current_item: IssuedQuestion | None


class DiagnosticReceipt(BaseModel):
    uuid: str
    attempt_uuid: str
    accepted_item_uuid: str
    version: int
    status: AttemptStatus
    accepted_at: datetime
    next_item: IssuedQuestion | None
    summary_ref: str | None


class DiagnosticSummary(BaseModel):
    attempt_uuid: str
    outcome: StopReason
    coverage_complete: bool
    answered_count: int
    skipped_count: int
    scored_count: int | None
    correct_count: int | None


async def _own_attempt(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    attempt_uuid: str,
    *,
    validate_pool: bool,
) -> tuple[StudyLearningAttempt, DiagnosticAccess | PracticeAccess, datetime]:
    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    scope = (
        await db.execute(
            select(StudyLearningAttempt.kind).where(
                StudyLearningAttempt.org_id == org.id,
                StudyLearningAttempt.actor_user_id == actor.id,
                StudyLearningAttempt.uuid == attempt_uuid,
                StudyLearningAttempt.kind.in_(("diagnostic", "practice")),
            )
        )
    ).scalar_one_or_none()
    if scope is None:
        raise HTTPException(404, "Learning attempt not found.")
    profile = await lock_attempt_learner(
        db,
        org,
        actor,
        now,
        PRACTICE_SCOPE if scope == "practice" else DIAGNOSTIC_SCOPE,
    )
    pair = (
        await db.execute(
            select(StudyLearningAttempt, StudyDiagnosticRevision.uuid)
            .outerjoin(
                StudyDiagnosticRevision,
                StudyDiagnosticRevision.id
                == StudyLearningAttempt.diagnostic_revision_id,
            )
            .where(
                StudyLearningAttempt.org_id == org.id,
                StudyLearningAttempt.learner_profile_id == profile.id,
                StudyLearningAttempt.actor_user_id == actor.id,
                StudyLearningAttempt.kind == scope,
                StudyLearningAttempt.uuid == attempt_uuid,
            )
            .with_for_update(of=StudyLearningAttempt)
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if pair is None:
        raise HTTPException(404, "Diagnostic attempt not found.")
    attempt, definition_uuid = pair
    if scope == "practice":
        issued = (
            await db.execute(
                select(StudyAttemptItem).where(
                    StudyAttemptItem.attempt_id == attempt.id,
                    StudyAttemptItem.org_id == org.id,
                    StudyAttemptItem.ordinal == 1,
                )
            )
        ).scalar_one()
        practice = await require_practice_access(
            request,
            db,
            org,
            actor,
            attempt.source_activity_uuid,
            now,
            item_id=issued.item_revision_id,
        )
        if attempt.manifest_digest != practice.manifest_digest:
            raise studytomy_http_error(
                409, STALE_VERSION, "The approved practice rules have changed."
            )
        return attempt, practice, now
    access = await require_diagnostic_access(
        request,
        db,
        org,
        actor,
        profile,
        definition_uuid,
        attempt.source_activity_uuid,
        now,
        validate_pool=validate_pool
        and attempt.status == "active"
        and now < _utc(attempt.expires_at),
    )
    if (
        attempt.manifest_digest != access.definition.content_digest
        or attempt.source_course_uuid != access.curriculum.owner_course_uuid
    ):
        raise HTTPException(404, "Diagnostic attempt not found.")
    return attempt, access, now


def _status(
    attempt: StudyLearningAttempt,
    access: DiagnosticAccess | PracticeAccess,
    now: datetime,
) -> AttemptStatus:
    if attempt.status != "active":
        return attempt.status
    if not access.available:
        return "invalidated"
    return "expired" if now >= _utc(attempt.expires_at) else "active"


async def _rows(
    db: AsyncSession, attempt: StudyLearningAttempt
) -> list[StudyAttemptItem]:
    return list(
        (
            await db.execute(
                select(StudyAttemptItem)
                .where(
                    StudyAttemptItem.org_id == attempt.org_id,
                    StudyAttemptItem.attempt_id == attempt.id,
                )
                .order_by(StudyAttemptItem.ordinal)
            )
        ).scalars()
    )


async def _item(
    db: AsyncSession,
    access: DiagnosticAccess | PracticeAccess,
    issued: StudyAttemptItem,
) -> StudyItemRevision:
    if (
        isinstance(access, PracticeAccess)
        and issued.item_revision_id == access.item.id
        and access.available
    ):
        return access.item
    item = await db.get(
        StudyItemRevision, issued.item_revision_id, populate_existing=True
    )
    if (
        not isinstance(access, DiagnosticAccess)
        or item is None
        or item.status != "published"
        or not any(
            candidate.uuid == item.uuid
            and candidate.content_digest == item.content_digest
            for candidate in access.manifest.pool
        )
    ):
        raise studytomy_http_error(
            409, STALE_VERSION, "The reviewed diagnostic item is unavailable."
        )
    return item


async def _public_attempt(
    db: AsyncSession,
    attempt: StudyLearningAttempt,
    access: DiagnosticAccess | PracticeAccess,
    now: datetime,
) -> AttemptRead:
    issued = await _rows(db, attempt)
    status = _status(attempt, access, now)
    outstanding = next(
        (entry for entry in issued if entry.response_state == "issued"), None
    )
    question = None
    if status == "active" and outstanding is not None:
        item = await _item(db, access, outstanding)
        question = IssuedQuestion(
            uuid=outstanding.uuid,
            ordinal=outstanding.ordinal,
            question=_QUESTION.validate_python(item.public_question),
        )
    return AttemptRead(
        uuid=attempt.uuid,
        purpose=attempt.kind,
        status=status,
        version=attempt.version,
        curriculum_uuid=access.curriculum.uuid,
        diagnostic_revision_uuid=access.definition.uuid
        if isinstance(access, DiagnosticAccess)
        else None,
        started_at=_utc(attempt.started_at),
        expires_at=_utc(attempt.expires_at),
        answered_count=sum(entry.response_state == "answered" for entry in issued),
        skipped_count=sum(entry.response_state == "skipped" for entry in issued),
        visited_concepts=len({entry.concept_id for entry in issued}),
        required_concepts=len(access.manifest.required_concepts)
        if isinstance(access, DiagnosticAccess)
        else 1,
        current_item=question,
    )


async def _close(
    db: AsyncSession,
    attempt: StudyLearningAttempt,
    actor: PublicUser,
    now: datetime,
    status: AttemptStatus,
    reason: StopReason,
) -> None:
    if attempt.status != "active":
        return
    for item in await _rows(db, attempt):
        if item.response_state == "issued":
            item.response_state = "skipped"
            item.responded_at = now
            item.response_version += 1
            item.skip_reason = reason
    await db.flush()
    attempt.status = status
    attempt.ended_at = now
    attempt.version += 1
    await append_business_event(
        db,
        org_id=attempt.org_id,
        actor_user_id=actor.id,
        action=_CLOSE
        if attempt.kind == "diagnostic"
        else "studytomy.adaptive.practice_attempt.closed",
        reason_code=reason.upper(),
        resource_kind=f"{attempt.kind}_attempt",
        resource_uuid=attempt.uuid,
        after_state={"status": status, "version": attempt.version},
        policy_refs=attempt.policy_version_ids,
    )


async def _history(
    db: AsyncSession, attempt: StudyLearningAttempt
) -> list[tuple[str, str]]:
    latest = (
        select(
            case(
                (
                    StudyLearningEvidence.eligible.is_(True),
                    StudyLearningEvidence.outcome,
                ),
                else_="not_scored",
            )
        )
        .where(
            StudyLearningEvidence.org_id == attempt.org_id,
            StudyLearningEvidence.attempt_item_id == StudyAttemptItem.id,
        )
        .order_by(StudyLearningEvidence.result_revision.desc())
        .limit(1)
        .correlate(StudyAttemptItem)
        .scalar_subquery()
    )
    rows = (
        await db.execute(
            select(
                StudyItemRevision.uuid,
                StudyAttemptItem.response_state,
                latest.label("outcome"),
            )
            .select_from(StudyAttemptItem)
            .join(
                StudyItemRevision,
                StudyItemRevision.id == StudyAttemptItem.item_revision_id,
            )
            .where(
                StudyAttemptItem.org_id == attempt.org_id,
                StudyAttemptItem.attempt_id == attempt.id,
                StudyAttemptItem.response_state != "issued",
            )
            .order_by(StudyAttemptItem.ordinal)
        )
    ).all()
    return [
        (
            row.uuid,
            "skip" if row.response_state == "skipped" else row.outcome or "not_scored",
        )
        for row in rows
    ]


async def _advance(
    db: AsyncSession,
    attempt: StudyLearningAttempt,
    access: DiagnosticAccess,
    actor: PublicUser,
    now: datetime,
) -> None:
    exposed = frozenset(
        (
            await db.execute(
                select(StudyAttemptItem.item_family_uuid)
                .where(
                    StudyAttemptItem.org_id == attempt.org_id,
                    StudyAttemptItem.learner_profile_id == attempt.learner_profile_id,
                    StudyAttemptItem.attempt_id != attempt.id,
                )
                .distinct()
            )
        ).scalars()
    )
    observed = tuple(
        DiagnosticObservation(uuid, outcome)
        for uuid, outcome in await _history(db, attempt)
    )
    decision = select_diagnostic_item(access.manifest, observed, exposed)
    if decision.item_uuid is None:
        await _close(db, attempt, actor, now, "completed", decision.stop_reason)
        return
    item = (
        await db.execute(
            select(StudyItemRevision)
            .where(
                StudyItemRevision.org_id == attempt.org_id,
                StudyItemRevision.uuid == decision.item_uuid,
                StudyItemRevision.status == "published",
                StudyItemRevision.curriculum_revision_id
                == attempt.curriculum_revision_id,
            )
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    if item is None:
        raise studytomy_http_error(
            409, STALE_VERSION, "The reviewed diagnostic item is unavailable."
        )
    await _issue(db, attempt, item, now)


async def _issue(
    db: AsyncSession,
    attempt: StudyLearningAttempt,
    item: StudyItemRevision,
    now: datetime,
) -> None:
    db.add(
        StudyAttemptItem(
            org_id=attempt.org_id,
            attempt_id=attempt.id,
            learner_profile_id=attempt.learner_profile_id,
            curriculum_revision_id=attempt.curriculum_revision_id,
            concept_id=item.concept_id,
            item_revision_id=item.id,
            item_family_uuid=item.item_family_uuid,
            ordinal=attempt.next_ordinal,
            is_diagnostic=attempt.kind == "diagnostic",
            issued_at=now,
        )
    )
    await db.flush()
    attempt.next_ordinal += 1
    attempt.version += 1
    await db.flush()


async def _complete(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    attempt: StudyLearningAttempt,
    operation: str,
    key: str,
    public: BaseModel,
    *,
    status_code: int = 200,
) -> None:
    await complete_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=operation,
        idempotency_key=key,
        resource_kind=f"{attempt.kind}_attempt",
        resource_uuid=attempt.uuid,
        response_status=status_code,
        response_projection=public.model_dump(mode="json"),
    )
    await db.commit()


async def start_diagnostic(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    body: DiagnosticStart,
    key: str,
) -> AttemptRead:
    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    profile = await lock_attempt_learner(db, org, actor, now, DIAGNOSTIC_SCOPE)
    access = await require_diagnostic_access(
        request,
        db,
        org,
        actor,
        profile,
        body.diagnostic_revision_uuid,
        body.source_activity_uuid,
        now,
        validate_pool=True,
    )
    if not access.available:
        raise studytomy_http_error(
            409, POLICY_NOT_APPROVED, "Start requires a current published diagnostic."
        )
    replay = await claim_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=_START,
        idempotency_key=key,
        request_payload=body.model_dump(mode="json"),
    )
    if replay is not None:
        attempt = (
            await db.execute(
                select(StudyLearningAttempt).where(
                    StudyLearningAttempt.org_id == org.id,
                    StudyLearningAttempt.learner_profile_id == profile.id,
                    StudyLearningAttempt.uuid == replay.resource_uuid,
                    StudyLearningAttempt.diagnostic_revision_id == access.definition.id,
                    StudyLearningAttempt.source_activity_uuid
                    == body.source_activity_uuid,
                )
            )
        ).scalar_one_or_none()
        if attempt is None:
            raise HTTPException(404, "Diagnostic attempt not found.")
        return await _public_attempt(db, attempt, access, now)
    rounds = list(
        (
            await db.execute(
                select(StudyLearningAttempt)
                .where(
                    StudyLearningAttempt.org_id == org.id,
                    StudyLearningAttempt.learner_profile_id == profile.id,
                    StudyLearningAttempt.blueprint_uuid
                    == access.definition.blueprint_uuid,
                )
                .order_by(
                    StudyLearningAttempt.started_at.desc(),
                    StudyLearningAttempt.id.desc(),
                )
                .limit(access.manifest.rules.max_attempts + 1)
                .with_for_update()
            )
        ).scalars()
    )
    active = next((row for row in rounds if row.status == "active"), None)
    if active is not None:
        if now < _utc(active.expires_at):
            raise HTTPException(
                409,
                detail={
                    "code": "DIAGNOSTIC_ALREADY_ACTIVE",
                    "message": "Resume your active diagnostic.",
                    "attempt_uuid": active.uuid,
                },
            )
        await _close(db, active, actor, _utc(active.expires_at), "expired", "expired")
    if len(rounds) >= access.manifest.rules.max_attempts:
        raise studytomy_http_error(
            409,
            "DIAGNOSTIC_ATTEMPT_LIMIT",
            "The approved attempt limit has been reached.",
        )
    last_ended = rounds[0].ended_at if rounds else None
    if last_ended and now < _utc(last_ended) + timedelta(
        minutes=access.manifest.rules.retake_wait_minutes
    ):
        raise studytomy_http_error(
            409,
            "DIAGNOSTIC_RETAKE_WAIT",
            "The approved retake interval has not elapsed.",
        )
    attempt = StudyLearningAttempt(
        org_id=org.id,
        learner_profile_id=profile.id,
        curriculum_revision_id=access.curriculum.id,
        diagnostic_revision_id=access.definition.id,
        blueprint_uuid=access.definition.blueprint_uuid,
        actor_user_id=actor.id,
        actor_capacity="learner",
        kind="diagnostic",
        source_course_uuid=access.curriculum.owner_course_uuid,
        source_activity_uuid=body.source_activity_uuid,
        manifest_digest=access.definition.content_digest,
        policy_version_ids=access.policy_ids,
        started_at=now,
        expires_at=now
        + timedelta(minutes=access.manifest.limits.expires_after_minutes),
    )
    db.add(attempt)
    await db.flush()
    await _advance(db, attempt, access, actor, now)
    public = await _public_attempt(db, attempt, access, now)
    await _complete(db, org, actor, attempt, _START, key, public, status_code=201)
    return public


class LessonAttemptOption(BaseModel):
    purpose: Literal["diagnostic", "practice"]
    uuid: str
    label: str


async def lesson_attempt_options(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    activity_uuid: str,
) -> list[LessonAttemptOption]:
    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    mapped = select(StudyConceptActivity.curriculum_revision_id).where(
        StudyConceptActivity.org_id == org.id,
        StudyConceptActivity.activity_uuid == activity_uuid,
        StudyConceptActivity.archived.is_(False),
    )
    options = []
    for purpose, scope in (
        ("diagnostic", DIAGNOSTIC_SCOPE),
        ("practice", PRACTICE_SCOPE),
    ):
        try:
            profile = await lock_attempt_learner(db, org, actor, now, scope)
        except HTTPException as error:
            if error.status_code not in {403, 404}:
                raise
            continue
        if purpose == "diagnostic":
            candidates = (
                await db.execute(
                    select(StudyDiagnosticRevision.uuid, StudyDiagnosticRevision.name)
                    .where(
                        StudyDiagnosticRevision.org_id == org.id,
                        StudyDiagnosticRevision.curriculum_revision_id.in_(mapped),
                        StudyDiagnosticRevision.status == "published",
                    )
                    .order_by(StudyDiagnosticRevision.id.desc())
                    .limit(30)
                )
            ).all()
        else:
            candidates = (
                await db.execute(
                    select(StudyConcept.uuid, StudyConcept.title)
                    .where(
                        StudyConcept.org_id == org.id,
                        StudyConcept.archived.is_(False),
                        StudyConcept.id.in_(
                            select(StudyConceptActivity.concept_id).where(
                                StudyConceptActivity.org_id == org.id,
                                StudyConceptActivity.activity_uuid == activity_uuid,
                                StudyConceptActivity.archived.is_(False),
                                StudyConceptActivity.usage == "practice",
                            )
                        ),
                    )
                    .order_by(StudyConcept.sequence, StudyConcept.id)
                    .limit(30)
                )
            ).all()
        for uuid, name in candidates:
            try:
                if purpose == "diagnostic":
                    await require_diagnostic_access(
                        request,
                        db,
                        org,
                        actor,
                        profile,
                        uuid,
                        activity_uuid,
                        now,
                        validate_pool=True,
                    )
                else:
                    await require_practice_access(
                        request,
                        db,
                        org,
                        actor,
                        activity_uuid,
                        now,
                        concept_uuid=uuid,
                    )
            except HTTPException as error:
                if error.status_code not in {403, 404, 409, 422}:
                    raise
                continue
            options.append(LessonAttemptOption(purpose=purpose, uuid=uuid, label=name))
    return options


async def read_current_attempt(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    source_activity_uuid: str,
    purpose: Literal["diagnostic", "practice"],
) -> AttemptRead | None:
    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    scope = DIAGNOSTIC_SCOPE if purpose == "diagnostic" else PRACTICE_SCOPE
    profile = await lock_attempt_learner(db, org, actor, now, scope)
    attempt_uuid = (
        await db.execute(
            select(StudyLearningAttempt.uuid)
            .where(
                StudyLearningAttempt.org_id == org.id,
                StudyLearningAttempt.learner_profile_id == profile.id,
                StudyLearningAttempt.actor_user_id == actor.id,
                StudyLearningAttempt.source_activity_uuid == source_activity_uuid,
                StudyLearningAttempt.kind == purpose,
                StudyLearningAttempt.status == "active",
                StudyLearningAttempt.expires_at > now,
            )
            .order_by(
                StudyLearningAttempt.started_at.desc(), StudyLearningAttempt.id.desc()
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if attempt_uuid is None:
        return None
    attempt, access, checked_at = await _own_attempt(
        request, db, org, actor, attempt_uuid, validate_pool=True
    )
    if _status(attempt, access, checked_at) != "active":
        return None
    return await _public_attempt(db, attempt, access, checked_at)


async def read_diagnostic_attempt(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    uuid: str,
) -> AttemptRead:
    attempt, access, now = await _own_attempt(
        request, db, org, actor, uuid, validate_pool=True
    )
    return await _public_attempt(db, attempt, access, now)


async def start_practice(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    body: PracticeStart,
    key: str,
) -> AttemptRead:
    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    profile = await lock_attempt_learner(db, org, actor, now, PRACTICE_SCOPE)
    replay = await claim_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=_PRACTICE_START,
        idempotency_key=key,
        request_payload=body.model_dump(mode="json"),
    )
    if replay is not None:
        return await read_diagnostic_attempt(
            request, db, org, actor, replay.resource_uuid
        )
    access = await require_practice_access(
        request,
        db,
        org,
        actor,
        body.source_activity_uuid,
        now,
        concept_uuid=body.concept_uuid,
    )
    attempt = StudyLearningAttempt(
        org_id=org.id,
        learner_profile_id=profile.id,
        curriculum_revision_id=access.curriculum.id,
        actor_user_id=actor.id,
        actor_capacity="learner",
        kind="practice",
        source_course_uuid=access.curriculum.owner_course_uuid,
        source_activity_uuid=access.activity_uuid,
        manifest_digest=access.manifest_digest,
        policy_version_ids=access.policy_ids,
        started_at=now,
        expires_at=now + timedelta(minutes=access.rules.expires_after_minutes),
    )
    db.add(attempt)
    await db.flush()
    await _issue(db, attempt, access.item, now)
    public = await _public_attempt(db, attempt, access, now)
    await _complete(
        db, org, actor, attempt, _PRACTICE_START, key, public, status_code=201
    )
    return public


async def abandon_diagnostic(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    uuid: str,
    body: DiagnosticAbandon,
    key: str,
) -> AttemptRead:
    attempt, access, now = await _own_attempt(
        request, db, org, actor, uuid, validate_pool=False
    )
    replay = await claim_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=_ABANDON,
        idempotency_key=key,
        request_payload={"attempt_uuid": uuid, "body": body.model_dump(mode="json")},
    )
    if replay is not None:
        return AttemptRead.model_validate(replay.response_projection)
    if attempt.status != "active" or attempt.version != body.expected_version:
        raise studytomy_http_error(
            409, STALE_VERSION, "Reload the current diagnostic attempt."
        )
    status = _status(attempt, access, now)
    reason = "abandoned" if status == "active" else status
    closed_at = _utc(attempt.expires_at) if reason == "expired" else now
    await _close(db, attempt, actor, closed_at, reason, reason)
    public = await _public_attempt(db, attempt, access, now)
    await _complete(db, org, actor, attempt, _ABANDON, key, public)
    return public


async def respond_to_diagnostic(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    uuid: str,
    body: DiagnosticResponse,
    key: str,
) -> DiagnosticReceipt:
    attempt, access, now = await _own_attempt(
        request, db, org, actor, uuid, validate_pool=True
    )
    replay = await claim_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=_RESPOND,
        idempotency_key=key,
        request_payload={"attempt_uuid": uuid, "body": body.model_dump(mode="json")},
    )
    if replay is not None:
        receipt = DiagnosticReceipt.model_validate(replay.response_projection)
        if not access.available:
            raise studytomy_http_error(
                409, POLICY_NOT_APPROVED, "The diagnostic is no longer available."
            )
        if receipt.next_item is not None and (
            _status(attempt, access, now) != "active"
            or attempt.version != receipt.version
        ):
            receipt.next_item = None
        return receipt
    if (
        _status(attempt, access, now) != "active"
        or attempt.version != body.expected_version
    ):
        raise studytomy_http_error(
            409, STALE_VERSION, "Reload the current diagnostic attempt."
        )
    issued = (
        await db.execute(
            select(StudyAttemptItem)
            .where(
                StudyAttemptItem.org_id == org.id,
                StudyAttemptItem.attempt_id == attempt.id,
                StudyAttemptItem.uuid == body.item_uuid,
                StudyAttemptItem.response_state == "issued",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if issued is None:
        raise HTTPException(404, "Issued question not found.")
    item = await _item(db, access, issued)
    if isinstance(body.response, SkipResponse):
        issued.response_state = "skipped"
        issued.skip_reason = "learner_skip"
    else:
        try:
            result = evaluate_objective_item(
                item, body.response.model_dump(mode="json")
            )
        except InvalidObjectiveResponse as error:
            raise studytomy_http_error(
                422, "DIAGNOSTIC_RESPONSE_INVALID", str(error)
            ) from error
        if result.normalized_response is None:
            raise studytomy_http_error(
                409, STALE_VERSION, "The reviewed question cannot accept a response."
            )
        issued.response_state = "answered"
        issued.normalized_response = result.normalized_response
        issued.response_digest = canonical_request_hash(
            {
                "item_uuid": issued.uuid,
                "normalized_response": result.normalized_response,
            }
        )
    issued.responded_at = now
    issued.response_version += 1
    await db.flush()
    await append_objective_evidence(
        db,
        org.id,
        actor,
        issued.uuid,
        access.policy,
        access.policy_ids,
        declared_external_help=body.declared_external_help,
    )
    if isinstance(access, PracticeAccess):
        await _close(db, attempt, actor, now, "completed", "practice_completed")
    else:
        await _advance(db, attempt, access, actor, now)
    public = await _public_attempt(db, attempt, access, now)
    receipt = DiagnosticReceipt(
        uuid=f"receipt_{uuid4()}",
        attempt_uuid=uuid,
        accepted_item_uuid=issued.uuid,
        version=public.version,
        status=public.status,
        accepted_at=now,
        next_item=public.current_item,
        summary_ref=(
            request.url_for(
                "api_read_diagnostic_summary", org_uuid=org.org_uuid, attempt_uuid=uuid
            ).path
            if public.status != "active"
            else None
        ),
    )
    await _complete(db, org, actor, attempt, _RESPOND, key, receipt)
    return receipt


async def read_diagnostic_summary(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    uuid: str,
) -> DiagnosticSummary:
    attempt, access, now = await _own_attempt(
        request, db, org, actor, uuid, validate_pool=False
    )
    public = await _public_attempt(db, attempt, access, now)
    if public.status == "active":
        raise studytomy_http_error(
            409,
            "DIAGNOSTIC_IN_PROGRESS",
            "The summary is available after the diagnostic closes.",
        )
    reason = public.status
    if isinstance(access, PracticeAccess):
        return DiagnosticSummary(
            attempt_uuid=uuid,
            outcome="practice_completed"
            if public.status == "completed"
            else public.status,
            coverage_complete=False,
            answered_count=public.answered_count,
            skipped_count=public.skipped_count,
            scored_count=None,
            correct_count=None,
        )
    if public.status == "completed":
        closed = (
            await db.execute(
                select(StudyBusinessEvent.reason).where(
                    StudyBusinessEvent.org_id == org.id,
                    StudyBusinessEvent.resource_uuid == uuid,
                    StudyBusinessEvent.action == _CLOSE,
                )
            )
        ).scalar_one_or_none()
        reason = closed.lower() if closed else "insufficient_items"
    scored_count = correct_count = None
    if (
        access.available
        and public.status == "completed"
        and access.manifest.rules.reveal == "after_close"
    ):
        outcomes = [outcome for _, outcome in await _history(db, attempt)]
        scored_count = sum(outcome in {"correct", "incorrect"} for outcome in outcomes)
        correct_count = outcomes.count("correct")
    return DiagnosticSummary(
        attempt_uuid=uuid,
        outcome=reason,
        coverage_complete=reason == "coverage_met",
        answered_count=public.answered_count,
        skipped_count=public.skipped_count,
        scored_count=scored_count,
        correct_count=correct_count,
    )
