"""Read the retained evidence required for an independent assessment review."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.assignments import (
    Assignment,
    AssignmentUserSubmission,
    AssignmentUserSubmissionStatus,
)
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.assessment_archive import StudyAssessmentResponseArchive
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyPolicyBody,
    StudyPolicyKey,
)
from src.db.users import PublicUser
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_studytomy_staff_action,
)
from src.services.studytomy.assignment_adapter import native_round_uuid
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.grading import (
    APPEAL_CORRECTION_REASON,
    RESULT_RECORDED_ACTION,
    latest_assessment_result,
    verified_retry_response_archive,
)
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization

REVIEW_POLICIES = (StudyPolicyKey.D_04, StudyPolicyKey.D_08, StudyPolicyKey.D_15)
DEADLINE_ADJUSTMENT_ACTION = "studytomy.assessment.deadline_adjusted"
RETRY_GRANTED_ACTION = "studytomy.assessment.retry_granted"


class AssessmentReviewEvidence(BaseModel):
    round_uuid: str
    result_uuid: str
    version: int
    score_points: int
    maximum_points: int
    passed: bool
    evaluator_version: str
    appeal: "AssessmentAppealReceipt | None" = None


class ArchivedAssessmentResponses(BaseModel):
    round_uuid: str
    responses: list[tuple[str, dict]]


class AssessmentAppealRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_uuid: UUID


class AssessmentAppealReceipt(BaseModel):
    appeal_uuid: str
    appealed_result_uuid: str
    result_uuid: str
    status: str
    decision_uuid: str | None = None


class AssessmentAppealAvailability(BaseModel):
    receipt: AssessmentAppealReceipt | None
    can_request_review: bool


class AssessmentAppealReopen(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_uuid: UUID


class AssessmentAppealCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score_points: int = Field(strict=True, ge=0)


class AssessmentDeadlineAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expires_at: AwareDatetime
    reason: Literal["SERVICE_INTERRUPTION", "APPROVED_ADJUSTMENT"]


class AssessmentDeadlineReceipt(BaseModel):
    adjustment_uuid: str
    expires_at: AwareDatetime


class AssessmentRetryGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_uuid: UUID
    expires_at: AwareDatetime


async def _retry_grant_policies(db: AsyncSession, org: Organization, now: datetime):
    policies = await _current_policies(db, org, REVIEW_POLICIES, now)
    assessment = next(
        policy for policy in policies if policy.policy_key == StudyPolicyKey.D_08.value
    )
    attempts = StudyPolicyBody.model_validate(assessment.body).specified_value(
        "attempts"
    )
    if (
        not isinstance(attempts, dict)
        or attempts.get("managed_retry_grants_enabled") is not True
    ):
        raise HTTPException(
            409, "Current policy does not approve managed retry grants."
        )
    return policies


async def record_assessment_retry_grant(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    assignment_uuid: str,
    submission_uuid: str,
    body: AssessmentRetryGrant,
    *,
    expected_attempt_number: int | None = None,
) -> AssessmentDeadlineReceipt:
    await lock_organization(db, org.id)
    evidence = await read_assessment_review_evidence(
        db,
        org,
        actor,
        course_uuid,
        assignment_uuid,
        submission_uuid,
        lock_attempt=True,
    )
    if evidence.result_uuid != str(body.result_uuid):
        raise HTTPException(
            409, "The retry must reference the current finalized result."
        )
    now = datetime.now(UTC)
    expires = body.expires_at.astimezone(UTC)
    if expires <= now:
        raise HTTPException(422, "The retry deadline must be in the future.")
    policies = await _retry_grant_policies(db, org, now)
    _, assignment, submission, _ = await _review_subject(
        db,
        org,
        course_uuid,
        assignment_uuid,
        submission_uuid,
    )
    if expected_attempt_number is not None and submission.attempt_number != expected_attempt_number:
        raise HTTPException(409, "The current assessment attempt changed.")
    await verified_retry_response_archive(db, assignment, submission)
    key = f"assessment_retry_grant:{evidence.round_uuid}"
    prior = await db.scalar(
        select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.dedupe_key == key,
        )
    )
    after_state = {
        "effective_to": expires.isoformat(),
        "result_uuid": evidence.result_uuid,
    }
    if prior is not None:
        if prior.after_state != after_state or prior.policy_refs != [
            policy.id for policy in policies
        ]:
            raise HTTPException(
                409, "This attempt already has a different retry decision."
            )
        return AssessmentDeadlineReceipt(
            adjustment_uuid=prior.event_uuid, expires_at=expires
        )
    grant = await append_business_event(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        actor_capacity="assessment_moderator",
        action=RETRY_GRANTED_ACTION,
        reason_code="INDEPENDENT_REVIEW_APPROVED_RETRY",
        resource_kind="assignment_round",
        resource_uuid=evidence.round_uuid,
        policy_refs=[policy.id for policy in policies],
        dedupe_key=key,
        after_state=after_state,
    )
    await db.commit()
    return AssessmentDeadlineReceipt(
        adjustment_uuid=grant.event_uuid, expires_at=expires
    )


async def resolve_assessment_retry_grant(
    db: AsyncSession,
    assignment: Assignment,
    submission: AssignmentUserSubmission,
) -> StudyBusinessEvent | None:
    round_uuid = native_round_uuid(
        assignment.org_id,
        submission.assignmentusersubmission_uuid,
        submission.attempt_number,
    )
    grant = await db.scalar(
        select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == assignment.org_id,
            StudyBusinessEvent.action == RETRY_GRANTED_ACTION,
            StudyBusinessEvent.dedupe_key == f"assessment_retry_grant:{round_uuid}",
        )
    )
    now = datetime.now(UTC)
    if (
        grant is None
        or datetime.fromisoformat(grant.after_state["effective_to"]) <= now
    ):
        return None
    result = await latest_assessment_result(db, assignment, submission)
    if result is None or result.event_uuid != grant.after_state["result_uuid"]:
        return None
    org = await db.get(Organization, assignment.org_id)
    try:
        policies = await _retry_grant_policies(db, org, now)
    except HTTPException as error:
        if error.status_code != 409:
            raise
        return None
    return grant if grant.policy_refs == [policy.id for policy in policies] else None


async def learner_reviewed_retry_deadline(
    db: AsyncSession,
    assignment: Assignment,
    submission: AssignmentUserSubmission,
) -> str | None:
    grant = await resolve_assessment_retry_grant(db, assignment, submission)
    if grant is None:
        return None
    try:
        await verified_retry_response_archive(db, assignment, submission)
    except HTTPException as error:
        if error.status_code != 409:
            raise
        return None
    return grant.after_state["effective_to"]


async def record_granted_retry_window(
    db: AsyncSession,
    assignment: Assignment,
    submission: AssignmentUserSubmission,
    grant: StudyBusinessEvent,
    actor: PublicUser,
) -> None:
    now = datetime.now(UTC)
    if datetime.fromisoformat(grant.after_state["effective_to"]) <= now:
        raise HTTPException(409, "The reviewed retry window has expired.")
    round_uuid = native_round_uuid(
        assignment.org_id,
        submission.assignmentusersubmission_uuid,
        submission.attempt_number,
    )
    await append_business_event(
        db,
        org_id=assignment.org_id,
        actor_user_id=actor.id,
        actor_capacity="learner",
        action=DEADLINE_ADJUSTMENT_ACTION,
        reason_code="APPROVED_RETRY_STARTED",
        resource_kind="assignment_round",
        resource_uuid=round_uuid,
        operation_uuid=grant.event_uuid,
        policy_refs=grant.policy_refs,
        dedupe_key=f"assessment_retry_consumed:{grant.event_uuid}",
        after_state={
            "effective_to": grant.after_state["effective_to"],
            "effective_from": now.isoformat(),
        },
    )


async def _review_subject(
    db: AsyncSession,
    org: Organization,
    course_uuid: str,
    assignment_uuid: str,
    submission_uuid: str,
    *,
    learner_id: int | None = None,
    lock_attempt: bool = False,
):
    statement = (
        select(Course, Assignment, AssignmentUserSubmission, StudyLearnerProfile)
        .join(Assignment, Assignment.course_id == Course.id)
        .join(
            AssignmentUserSubmission,
            AssignmentUserSubmission.assignment_id == Assignment.id,
        )
        .join(
            StudyLearnerProfile,
            StudyLearnerProfile.user_id == AssignmentUserSubmission.user_id,
        )
        .where(
            Course.org_id == org.id,
            Course.course_uuid == course_uuid,
            Course.studytomy_managed.is_(True),
            Assignment.org_id == org.id,
            Assignment.assignment_uuid == assignment_uuid,
            AssignmentUserSubmission.assignmentusersubmission_uuid == submission_uuid,
            StudyLearnerProfile.org_id == org.id,
        )
    )
    if learner_id is not None:
        statement = statement.where(AssignmentUserSubmission.user_id == learner_id)
    if lock_attempt:
        statement = statement.with_for_update(
            of=AssignmentUserSubmission
        ).execution_options(populate_existing=True)
    row = (await db.execute(statement)).one_or_none()
    if row is None:
        raise HTTPException(404, "Assessment review evidence not found.")
    return row


async def record_assessment_deadline_adjustment(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    assignment_uuid: str,
    submission_uuid: str,
    body: AssessmentDeadlineAdjustment,
    *,
    expected_attempt_number: int | None = None,
) -> AssessmentDeadlineReceipt:
    await lock_organization(db, org.id)
    course, assignment, submission, profile = await _review_subject(
        db,
        org,
        course_uuid,
        assignment_uuid,
        submission_uuid,
        lock_attempt=True,
    )
    now = datetime.now(UTC)
    for scope, resource_uuid in (
        ("course", course.course_uuid),
        ("learner", profile.learner_uuid),
    ):
        await authorize_studytomy_staff_action(
            db,
            actor,
            org,
            StudytomyCapabilityName.ASSESSMENT_MODERATION,
            target_scope_kind=scope,
            target_resource_uuid=resource_uuid,
            subject_user_id=profile.user_id,
            hide_existence_on_denial=True,
            now=now,
        )
    policies = await _current_policies(db, org, REVIEW_POLICIES, now)
    if expected_attempt_number is not None and submission.attempt_number != expected_attempt_number:
        raise HTTPException(409, "The current assessment attempt changed.")
    if submission.submission_status not in (
        AssignmentUserSubmissionStatus.PENDING,
        AssignmentUserSubmissionStatus.NOT_SUBMITTED,
    ):
        raise HTTPException(409, "A deadline adjustment requires an open attempt.")
    deadline = body.expires_at.astimezone(UTC)
    if deadline <= now:
        raise HTTPException(422, "The adjusted deadline must be in the future.")
    from src.services.courses.activities.assignments import _assignment_deadline

    original = _assignment_deadline(assignment)
    if original is None or deadline <= original.astimezone(UTC):
        raise HTTPException(422, "The adjustment must extend a scheduled deadline.")
    round_uuid = native_round_uuid(
        org.id,
        submission.assignmentusersubmission_uuid,
        submission.attempt_number,
    )
    previous = await _current_deadline_adjustment(db, org.id, round_uuid)
    if previous is not None:
        recorded_deadline = datetime.fromisoformat(previous.after_state["effective_to"])
        if deadline == recorded_deadline and body.reason == previous.reason:
            return AssessmentDeadlineReceipt(
                adjustment_uuid=previous.event_uuid, expires_at=deadline
            )
        if deadline <= recorded_deadline:
            raise HTTPException(
                409, "The adjustment cannot shorten the current deadline."
            )
    event = await append_business_event(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        actor_capacity="assessment_moderator",
        action=DEADLINE_ADJUSTMENT_ACTION,
        reason_code=body.reason,
        subject_uuid=profile.learner_uuid,
        resource_kind="assignment_round",
        resource_uuid=round_uuid,
        policy_refs=[policy.id for policy in policies],
        after_state={
            "effective_from": now.isoformat(),
            "effective_to": deadline.isoformat(),
            "previous_event_uuid": previous.event_uuid if previous else None,
        },
    )
    await db.commit()
    return AssessmentDeadlineReceipt(
        adjustment_uuid=event.event_uuid, expires_at=deadline
    )


async def _current_deadline_adjustment(
    db: AsyncSession,
    org_id: int,
    round_uuid: str,
) -> StudyBusinessEvent | None:
    return await db.scalar(
        select(StudyBusinessEvent)
        .where(
            StudyBusinessEvent.org_id == org_id,
            StudyBusinessEvent.action == DEADLINE_ADJUSTMENT_ACTION,
            StudyBusinessEvent.resource_uuid == round_uuid,
        )
        .order_by(StudyBusinessEvent.id.desc())
        .limit(1)
    )


async def assessment_deadline_extension(
    db: AsyncSession,
    assignment: Assignment,
    learner_id: int,
) -> datetime | None:
    submission = await db.scalar(
        select(AssignmentUserSubmission).where(
            AssignmentUserSubmission.assignment_id == assignment.id,
            AssignmentUserSubmission.user_id == learner_id,
            AssignmentUserSubmission.submission_status.in_(
                (
                    AssignmentUserSubmissionStatus.PENDING,
                    AssignmentUserSubmissionStatus.NOT_SUBMITTED,
                )
            ),
        )
    )
    if submission is None:
        return None
    round_uuid = native_round_uuid(
        assignment.org_id,
        submission.assignmentusersubmission_uuid,
        submission.attempt_number,
    )
    event = await _current_deadline_adjustment(db, assignment.org_id, round_uuid)
    now = datetime.now(UTC)
    if event is None:
        return None
    deadline = datetime.fromisoformat(event.after_state["effective_to"])
    if now >= deadline:
        return None
    org = await db.get(Organization, assignment.org_id)
    if org is None:
        return None
    try:
        if event.reason == "APPROVED_RETRY_STARTED":
            policies = await _retry_grant_policies(db, org, now)
            if event.policy_refs != [policy.id for policy in policies]:
                return None
        else:
            await _current_policies(db, org, REVIEW_POLICIES, now)
    except HTTPException as error:
        if error.status_code != 409:
            raise
        return None
    return deadline


async def read_assessment_review_evidence(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    assignment_uuid: str,
    submission_uuid: str,
    *,
    lock_attempt: bool = False,
    attempt_number: int | None = None,
    include_appeal: bool = False,
) -> AssessmentReviewEvidence:
    unavailable = HTTPException(404, "Assessment review evidence not found.")
    row = await _review_subject(
        db,
        org,
        course_uuid,
        assignment_uuid,
        submission_uuid,
        lock_attempt=lock_attempt,
    )
    course, assignment, submission, profile = row
    now = datetime.now(UTC)
    for scope, resource_uuid in (
        ("course", course.course_uuid),
        ("learner", profile.learner_uuid),
    ):
        await authorize_studytomy_staff_action(
            db,
            actor,
            org,
            StudytomyCapabilityName.ASSESSMENT_MODERATION,
            target_scope_kind=scope,
            target_resource_uuid=resource_uuid,
            subject_user_id=profile.user_id,
            hide_existence_on_denial=True,
            now=now,
        )
    await _current_policies(db, org, REVIEW_POLICIES, now)
    if (
        attempt_number is not None
        and not 1 <= attempt_number <= submission.attempt_number
    ):
        raise unavailable
    result = await latest_assessment_result(
        db, assignment, submission, attempt_number=attempt_number
    )
    if result is None:
        raise unavailable
    if await _actor_marked_round(db, org.id, actor.id, result.resource_uuid):
        raise unavailable

    state = result.after_state
    appeal = None
    if include_appeal:
        request = await db.scalar(
            select(StudyBusinessEvent)
            .where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.actor_user_id == profile.user_id,
                StudyBusinessEvent.action == "studytomy.assessment.appeal_requested",
                StudyBusinessEvent.operation_uuid == result.resource_uuid,
            )
            .order_by(StudyBusinessEvent.id.desc())
            .limit(1)
        )
        if request is not None:
            appeal = await _appeal_receipt(db, request)
    return AssessmentReviewEvidence(
        round_uuid=result.resource_uuid,
        result_uuid=result.event_uuid,
        version=state["version"],
        score_points=state["score_points"],
        maximum_points=state["maximum_points"],
        passed=state["passed"],
        evaluator_version=state["evaluator_version"],
        appeal=appeal,
    )


async def _actor_marked_round(
    db: AsyncSession, org_id: int, actor_id: int, round_uuid: str
) -> bool:
    return (
        await db.scalar(
            select(StudyBusinessEvent.id)
            .where(
                StudyBusinessEvent.org_id == org_id,
                StudyBusinessEvent.actor_user_id == actor_id,
                or_(
                    and_(
                        StudyBusinessEvent.resource_uuid == round_uuid,
                        StudyBusinessEvent.action == RESULT_RECORDED_ACTION,
                    ),
                    and_(
                        StudyBusinessEvent.operation_uuid == round_uuid,
                        StudyBusinessEvent.action == "studytomy.assessment.task_marked",
                    ),
                ),
            )
            .limit(1)
        )
        is not None
    )


async def read_archived_assessment_responses(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    assignment_uuid: str,
    submission_uuid: str,
    attempt_number: int,
) -> ArchivedAssessmentResponses:
    denied = HTTPException(404, "Archived assessment responses not found.")
    course, _, submission, profile = await _review_subject(
        db,
        org,
        course_uuid,
        assignment_uuid,
        submission_uuid,
    )
    now = datetime.now(UTC)
    for kind, uuid in (
        ("course", course.course_uuid),
        ("learner", profile.learner_uuid),
    ):
        await authorize_studytomy_staff_action(
            db,
            actor,
            org,
            StudytomyCapabilityName.ASSESSMENT_MODERATION,
            target_scope_kind=kind,
            target_resource_uuid=uuid,
            subject_user_id=profile.user_id,
            hide_existence_on_denial=True,
            now=now,
        )
    if not 1 <= attempt_number < submission.attempt_number:
        raise denied
    round_uuid = native_round_uuid(
        org.id, submission.assignmentusersubmission_uuid, attempt_number
    )
    if await _actor_marked_round(db, org.id, actor.id, round_uuid):
        raise denied
    (policy,) = await _current_policies(db, org, (StudyPolicyKey.D_13,), now)
    access = StudyPolicyBody.model_validate(policy.body).specified_value(
        "record_access"
    )
    if (
        not isinstance(access, dict)
        or access.get("managed_assessment_archive_review_enabled") is not True
    ):
        raise HTTPException(
            409,
            "Current policy does not approve access to archived assessment responses.",
        )
    archive = await db.get(StudyAssessmentResponseArchive, round_uuid)
    if (
        archive is None
        or archive.org_id != org.id
        or archive.user_id != profile.user_id
    ):
        raise denied
    if (
        archive.retain_until.replace(tzinfo=UTC)
        if archive.retain_until.tzinfo is None
        else archive.retain_until
    ) <= now:
        raise denied
    if (
        canonical_request_hash([round_uuid, archive.responses])
        != archive.content_digest
    ):
        raise HTTPException(409, "The archived response record could not be verified.")
    await append_business_event(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        actor_capacity="assessment_moderator",
        action="studytomy.assessment.archive_read",
        reason_code="AUTHORIZED_ARCHIVE_REVIEW",
        subject_uuid=profile.learner_uuid,
        resource_kind="assignment_round",
        resource_uuid=round_uuid,
        policy_refs=[policy.id],
    )
    await db.commit()
    return ArchivedAssessmentResponses(
        round_uuid=round_uuid, responses=archive.responses
    )


async def request_assessment_appeal(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    assignment_uuid: str,
    submission_uuid: str,
    body: AssessmentAppealRequest,
) -> AssessmentAppealReceipt:
    await lock_organization(db, org.id)
    _, assignment, submission, profile = await _review_subject(
        db,
        org,
        course_uuid,
        assignment_uuid,
        submission_uuid,
        learner_id=actor.id,
        lock_attempt=True,
    )

    policies = await _current_policies(db, org, REVIEW_POLICIES, datetime.now(UTC))
    result = await latest_assessment_result(db, assignment, submission)
    if result is None or result.event_uuid != str(body.result_uuid):
        raise HTTPException(
            409, "An appeal must reference the current finalized result."
        )

    dedupe_key = f"assessment_appeal:{org.id}:{result.event_uuid}"
    event = (
        await db.execute(
            select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.dedupe_key == dedupe_key,
            )
        )
    ).scalar_one_or_none()
    if event is None:
        event = await append_business_event(
            db,
            org_id=org.id,
            actor_user_id=actor.id,
            actor_capacity="learner",
            action="studytomy.assessment.appeal_requested",
            reason_code="LEARNER_REQUESTED_REVIEW",
            subject_uuid=profile.learner_uuid,
            resource_kind="assignment_result",
            resource_uuid=result.event_uuid,
            operation_uuid=result.resource_uuid,
            policy_refs=[policy.id for policy in policies],
            dedupe_key=dedupe_key,
        )
        await db.commit()
    return await _appeal_receipt(db, event)


async def read_latest_assessment_appeal(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    assignment_uuid: str,
    submission_uuid: str,
) -> AssessmentAppealAvailability:
    _, assignment, submission, _ = await _review_subject(
        db,
        org,
        course_uuid,
        assignment_uuid,
        submission_uuid,
        learner_id=actor.id,
    )
    result = await latest_assessment_result(db, assignment, submission)
    if result is None:
        raise HTTPException(404, "Assessment review evidence not found.")
    request = await db.scalar(
        select(StudyBusinessEvent)
        .where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.actor_user_id == actor.id,
            StudyBusinessEvent.action == "studytomy.assessment.appeal_requested",
            StudyBusinessEvent.operation_uuid == result.resource_uuid,
        )
        .order_by(StudyBusinessEvent.id.desc())
        .limit(1)
    )
    receipt = await _appeal_receipt(db, request) if request is not None else None
    if receipt is not None and receipt.appealed_result_uuid == result.event_uuid:
        return AssessmentAppealAvailability(receipt=receipt, can_request_review=False)
    try:
        await _current_policies(db, org, REVIEW_POLICIES, datetime.now(UTC))
    except HTTPException as error:
        if (
            receipt is None
            or error.status_code != 409
            or not isinstance(error.detail, dict)
            or not str(error.detail.get("code", "")).startswith("POLICY_")
        ):
            raise
        return AssessmentAppealAvailability(receipt=receipt, can_request_review=False)
    return AssessmentAppealAvailability(receipt=receipt, can_request_review=True)


async def _appeal_receipt(
    db: AsyncSession, request: StudyBusinessEvent
) -> AssessmentAppealReceipt:
    outcome = await db.scalar(
        select(StudyBusinessEvent)
        .where(
            StudyBusinessEvent.org_id == request.org_id,
            StudyBusinessEvent.resource_kind == "assessment_appeal",
            StudyBusinessEvent.resource_uuid == request.event_uuid,
            StudyBusinessEvent.action.in_(
                (
                    "studytomy.assessment.appeal_confirmed",
                    "studytomy.assessment.appeal_corrected",
                    "studytomy.assessment.appeal_reopened",
                )
            ),
        )
        .order_by(StudyBusinessEvent.id.desc())
        .limit(1)
    )
    return AssessmentAppealReceipt(
        appeal_uuid=request.event_uuid,
        appealed_result_uuid=request.resource_uuid,
        result_uuid=outcome.after_state["result_uuid"]
        if outcome and outcome.after_state
        else request.resource_uuid,
        status=outcome.after_state["status"]
        if outcome and outcome.after_state
        else ("result_confirmed" if outcome else "received"),
        decision_uuid=outcome.event_uuid if outcome else None,
    )


async def read_own_assessment_appeal(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    appeal_uuid: str,
) -> AssessmentAppealReceipt:
    request = (
        await db.execute(
            select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.event_uuid == appeal_uuid,
                StudyBusinessEvent.actor_user_id == actor.id,
                StudyBusinessEvent.action == "studytomy.assessment.appeal_requested",
            )
        )
    ).scalar_one_or_none()
    if request is None:
        raise HTTPException(404, "Assessment appeal not found.")
    return await _appeal_receipt(db, request)


async def _lock_appeal_review(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    assignment_uuid: str,
    submission_uuid: str,
    appeal_uuid: str,
    attempt_number: int | None = None,
) -> tuple[StudyBusinessEvent, AssessmentReviewEvidence]:
    await lock_organization(db, org.id)
    evidence = await read_assessment_review_evidence(
        db,
        org,
        actor,
        course_uuid,
        assignment_uuid,
        submission_uuid,
        lock_attempt=True,
        attempt_number=attempt_number,
    )
    request = (
        await db.execute(
            select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.event_uuid == appeal_uuid,
                StudyBusinessEvent.action == "studytomy.assessment.appeal_requested",
                StudyBusinessEvent.operation_uuid == evidence.round_uuid,
            )
        )
    ).scalar_one_or_none()
    if request is None or request.actor_user_id == actor.id:
        raise HTTPException(404, "Assessment appeal not found.")
    return request, evidence


async def decide_assessment_appeal(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    assignment_uuid: str,
    submission_uuid: str,
    appeal_uuid: str,
    *,
    correction: AssessmentAppealCorrection | None = None,
    expected_decision_uuid: UUID | None = None,
    attempt_number: int | None = None,
) -> AssessmentAppealReceipt:
    request, evidence = await _lock_appeal_review(
        db,
        org,
        actor,
        course_uuid,
        assignment_uuid,
        submission_uuid,
        appeal_uuid,
        attempt_number,
    )
    receipt = await _appeal_receipt(db, request)
    if receipt.status not in ("received", "reopened"):
        if correction is not None or receipt.status != "result_confirmed":
            raise HTTPException(409, "The appeal already has a recorded decision.")
        return receipt
    if receipt.status == "reopened" and receipt.decision_uuid != str(
        expected_decision_uuid
    ):
        raise HTTPException(
            409, "Refresh the reopened appeal before issuing another decision."
        )
    if request.resource_uuid != evidence.result_uuid:
        raise HTTPException(409, "The appealed result has been superseded.")
    policies = await _current_policies(db, org, REVIEW_POLICIES, datetime.now(UTC))
    policy_refs = [policy.id for policy in policies]
    if correction is not None:
        _, assignment, submission, _ = await _review_subject(
            db, org, course_uuid, assignment_uuid, submission_uuid
        )
        previous = await latest_assessment_result(
            db, assignment, submission, attempt_number=attempt_number
        )
        if (
            correction.score_points > evidence.maximum_points
            or correction.score_points == evidence.score_points
        ):
            raise HTTPException(
                422, "The correction must change the score within its original bounds."
            )
        state = previous.after_state
        version = evidence.version + 1
        revised = await append_business_event(
            db,
            org_id=org.id,
            actor_user_id=actor.id,
            actor_capacity="assessment_moderator",
            action=RESULT_RECORDED_ACTION,
            reason_code=APPEAL_CORRECTION_REASON,
            resource_kind="assignment_round",
            resource_uuid=evidence.round_uuid,
            policy_refs=policy_refs,
            dedupe_key=f"assessment_result:{evidence.round_uuid}:{version}",
            after_state={
                **state,
                "score_points": correction.score_points,
                "passed": 100 * correction.score_points / evidence.maximum_points
                >= state["passing_threshold"],
                "result_digest": None,
                "version": version,
                "previous_event_uuid": previous.event_uuid,
            },
        )
        if attempt_number is None or attempt_number == submission.attempt_number:
            submission.grade = correction.score_points
            db.add(submission)
        receipt.result_uuid = revised.event_uuid
        receipt.status = "result_corrected"
    else:
        receipt.status = "result_confirmed"
    outcome = await append_business_event(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        actor_capacity="assessment_moderator",
        action="studytomy.assessment.appeal_corrected"
        if correction
        else "studytomy.assessment.appeal_confirmed",
        reason_code="RESULT_CORRECTED_AFTER_REVIEW"
        if correction
        else "RESULT_CONFIRMED_AFTER_REVIEW",
        subject_uuid=request.subject_uuid,
        resource_kind="assessment_appeal",
        resource_uuid=appeal_uuid,
        operation_uuid=evidence.round_uuid,
        policy_refs=policy_refs,
        after_state={
            "result_uuid": receipt.result_uuid,
            "status": receipt.status,
            "previous_event_uuid": receipt.decision_uuid,
        },
        dedupe_key=f"assessment_appeal_decision:{appeal_uuid}:{receipt.decision_uuid or 'initial'}",
    )
    await db.commit()
    receipt.decision_uuid = outcome.event_uuid
    return receipt


async def reopen_assessment_appeal(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    assignment_uuid: str,
    submission_uuid: str,
    appeal_uuid: str,
    body: AssessmentAppealReopen,
    *,
    attempt_number: int | None = None,
) -> AssessmentAppealReceipt:
    request, evidence = await _lock_appeal_review(
        db,
        org,
        actor,
        course_uuid,
        assignment_uuid,
        submission_uuid,
        appeal_uuid,
        attempt_number,
    )
    receipt = await _appeal_receipt(db, request)
    dedupe_key = f"assessment_appeal_reopen:{appeal_uuid}:{body.decision_uuid}"
    if (
        receipt.status == "reopened"
        and await db.scalar(
            select(StudyBusinessEvent.id).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.event_uuid == receipt.decision_uuid,
                StudyBusinessEvent.dedupe_key == dedupe_key,
            )
        )
        is not None
    ):
        return receipt
    if (
        receipt.status != "result_confirmed"
        or receipt.decision_uuid != str(body.decision_uuid)
        or receipt.result_uuid != evidence.result_uuid
    ):
        raise HTTPException(409, "Only the current confirmed decision can be reopened.")
    policies = await _current_policies(db, org, REVIEW_POLICIES, datetime.now(UTC))
    outcome = await append_business_event(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        actor_capacity="assessment_moderator",
        action="studytomy.assessment.appeal_reopened",
        reason_code="MODERATOR_REOPENED_APPEAL",
        subject_uuid=request.subject_uuid,
        resource_kind="assessment_appeal",
        resource_uuid=appeal_uuid,
        operation_uuid=evidence.round_uuid,
        policy_refs=[policy.id for policy in policies],
        dedupe_key=dedupe_key,
        after_state={
            "status": "reopened",
            "result_uuid": evidence.result_uuid,
            "previous_event_uuid": receipt.decision_uuid,
        },
    )
    await db.commit()
    return AssessmentAppealReceipt(
        appeal_uuid=appeal_uuid,
        appealed_result_uuid=request.resource_uuid,
        result_uuid=evidence.result_uuid,
        status="reopened",
        decision_uuid=outcome.event_uuid,
    )
