"""Human intake and learner response for managed assessment concerns."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import aliased
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.assignments import AssignmentUserSubmissionStatus
from src.db.organizations import Organization
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
from src.services.studytomy.assessment_reviews import (
    REVIEW_POLICIES,
    _actor_marked_round,
    _review_subject,
)
from src.services.studytomy.assignment_adapter import native_round_uuid
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization

CONCERN_RECORDED = "studytomy.assessment.integrity_concern_recorded"
RESPONSE_RECORDED = "studytomy.assessment.integrity_response_recorded"
CONCERN_DISMISSED = "studytomy.assessment.integrity_concern_dismissed"


class IntegrityConcern(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["staff_observation", "automated_signal"]


class IntegrityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response: Literal["dispute_concern", "request_staff_contact"]


class IntegrityIntakeAvailability(BaseModel):
    can_record: bool


class IntegrityReceipt(BaseModel):
    reference: str
    status: Literal["awaiting_review", "response_received", "dismissed"]


class IntegrityReviewReceipt(IntegrityReceipt):
    response: Literal["dispute_concern", "request_staff_contact"] | None


class IntegrityDismissal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Literal["insufficient_evidence", "concern_resolved"]


async def _integrity_policy_refs(
    db: AsyncSession, org: Organization, now: datetime
) -> list[int]:
    policies = await _current_policies(db, org, REVIEW_POLICIES, now)
    assessment = next(
        policy for policy in policies if policy.policy_key == StudyPolicyKey.D_08.value
    )
    assistance = StudyPolicyBody.model_validate(assessment.body).specified_value(
        "assistance"
    )
    if (
        not isinstance(assistance, dict)
        or assistance.get("managed_integrity_review_enabled") is not True
    ):
        raise HTTPException(
            409, "Current policy does not approve assessment integrity review."
        )
    return [policy.id for policy in policies]


async def _integrity_intake(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    assignment_uuid: str,
    submission_uuid: str,
    expected_attempt: int | None,
) -> tuple[str, str, list[int], StudyBusinessEvent | None]:
    await lock_organization(db, org.id)
    course, _, submission, profile = await _review_subject(
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
    policy_refs = await _integrity_policy_refs(db, org, now)
    if submission.submission_status not in (
        AssignmentUserSubmissionStatus.SUBMITTED,
        AssignmentUserSubmissionStatus.GRADED,
    ):
        raise HTTPException(409, "Integrity intake requires a handed-in attempt.")
    if expected_attempt is not None and expected_attempt != submission.attempt_number:
        raise HTTPException(409, "The current submission attempt has changed.")
    round_uuid = native_round_uuid(
        org.id, submission.assignmentusersubmission_uuid, submission.attempt_number
    )
    dedupe = f"assessment_integrity:{round_uuid}"
    case = await db.scalar(
        select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.dedupe_key == dedupe,
        )
    )
    return round_uuid, profile.learner_uuid, policy_refs, case


async def read_integrity_intake_availability(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    assignment_uuid: str,
    submission_uuid: str,
    expected_attempt: int,
) -> IntegrityIntakeAvailability:
    _, _, _, case = await _integrity_intake(
        db, org, actor, course_uuid, assignment_uuid, submission_uuid, expected_attempt,
    )
    return IntegrityIntakeAvailability(can_record=case is None)


async def record_integrity_concern(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    assignment_uuid: str,
    submission_uuid: str,
    body: IntegrityConcern,
    expected_attempt: int | None = None,
) -> IntegrityReceipt:
    round_uuid, learner_uuid, policy_refs, case = await _integrity_intake(
        db, org, actor, course_uuid, assignment_uuid, submission_uuid, expected_attempt,
    )
    reason = (
        "STAFF_OBSERVATION"
        if body.source == "staff_observation"
        else "AUTOMATED_SIGNAL_REFERRED"
    )
    if case is not None and case.reason != reason:
        raise HTTPException(409, "A concern is already recorded for this attempt.")
    if case is None:
        case = await append_business_event(
            db,
            org_id=org.id,
            actor_user_id=actor.id,
            actor_capacity="assessment_moderator",
            action=CONCERN_RECORDED,
            reason_code=reason,
            subject_uuid=learner_uuid,
            resource_kind="assignment_round",
            resource_uuid=round_uuid,
            policy_refs=policy_refs,
            dedupe_key=f"assessment_integrity:{round_uuid}",
        )
        await db.commit()
    if await _case_dismissal(db, org.id, case.event_uuid):
        return IntegrityReceipt(reference=case.event_uuid, status="dismissed")
    response = await _case_response(db, org.id, case.event_uuid)
    return IntegrityReceipt(
        reference=case.event_uuid,
        status="response_received" if response else "awaiting_review",
    )


async def _case_response(
    db: AsyncSession, org_id: int, reference: str
) -> StudyBusinessEvent | None:
    return await db.scalar(
        select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == org_id,
            StudyBusinessEvent.action == RESPONSE_RECORDED,
            StudyBusinessEvent.dedupe_key
            == f"assessment_integrity_response:{reference}",
        )
    )


async def _case_dismissal(
    db: AsyncSession, org_id: int, reference: str
) -> StudyBusinessEvent | None:
    return await db.scalar(
        select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == org_id,
            StudyBusinessEvent.action == CONCERN_DISMISSED,
            StudyBusinessEvent.dedupe_key
            == f"assessment_integrity_dismissal:{reference}",
        )
    )


async def round_has_open_integrity_concern(
    db: AsyncSession, org_id: int, round_uuid: str
) -> bool:
    case = await db.scalar(
        select(StudyBusinessEvent.event_uuid).where(
            StudyBusinessEvent.org_id == org_id,
            StudyBusinessEvent.action == CONCERN_RECORDED,
            StudyBusinessEvent.dedupe_key == f"assessment_integrity:{round_uuid}",
        )
    )
    return case is not None and await _case_dismissal(db, org_id, case) is None


async def _authorized_integrity_review(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    assignment_uuid: str,
    submission_uuid: str,
    reference: str | None,
    attempt_number: int | None,
) -> tuple[StudyBusinessEvent, list[int]]:
    await lock_organization(db, org.id)
    course, _, submission, profile = await _review_subject(
        db,
        org,
        course_uuid,
        assignment_uuid,
        submission_uuid,
        lock_attempt=True,
    )
    denied = HTTPException(404, "Assessment integrity review not found.")
    attempt_number = (
        submission.attempt_number if attempt_number is None else attempt_number
    )
    if not 1 <= attempt_number <= submission.attempt_number:
        raise denied
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
    round_uuid = native_round_uuid(
        org.id, submission.assignmentusersubmission_uuid, attempt_number
    )
    case = await db.scalar(
        select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.action == CONCERN_RECORDED,
            StudyBusinessEvent.dedupe_key == f"assessment_integrity:{round_uuid}",
            StudyBusinessEvent.resource_uuid == round_uuid,
            StudyBusinessEvent.subject_uuid == profile.learner_uuid,
        )
    )
    if (
        case is None
        or (reference is not None and case.event_uuid != reference)
        or case.actor_user_id == actor.id
        or await _actor_marked_round(db, org.id, actor.id, round_uuid)
    ):
        raise denied
    return case, await _integrity_policy_refs(db, org, now)


async def read_integrity_review(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    assignment_uuid: str,
    submission_uuid: str,
    reference: str | None,
    attempt_number: int | None = None,
) -> IntegrityReviewReceipt:
    case, _ = await _authorized_integrity_review(
        db, org, actor, course_uuid, assignment_uuid, submission_uuid, reference, attempt_number
    )
    reference = case.event_uuid
    reply = await _case_response(db, org.id, reference)
    dismissal = await _case_dismissal(db, org.id, reference)
    status = "dismissed" if dismissal else "response_received" if reply else "awaiting_review"
    return IntegrityReviewReceipt(
        reference=reference,
        status=status,
        response=reply.reason.lower() if reply else None,
    )


async def dismiss_integrity_concern(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    assignment_uuid: str,
    submission_uuid: str,
    reference: str,
    body: IntegrityDismissal,
    attempt_number: int | None = None,
) -> IntegrityReceipt:
    case, policy_refs = await _authorized_integrity_review(
        db, org, actor, course_uuid, assignment_uuid, submission_uuid, reference, attempt_number
    )
    dismissal = await _case_dismissal(db, org.id, reference)
    reason = body.reason.upper()
    if dismissal is not None and dismissal.reason != reason:
        raise HTTPException(409, "A different review outcome is already recorded.")
    if dismissal is None:
        await append_business_event(
            db,
            org_id=org.id,
            actor_user_id=actor.id,
            actor_capacity="assessment_moderator",
            action=CONCERN_DISMISSED,
            reason_code=reason,
            subject_uuid=case.subject_uuid,
            resource_kind="assessment_integrity_case",
            resource_uuid=reference,
            operation_uuid=case.resource_uuid,
            policy_refs=policy_refs,
            dedupe_key=f"assessment_integrity_dismissal:{reference}",
        )
        await db.commit()
    return IntegrityReceipt(reference=reference, status="dismissed")


async def learner_integrity_case(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    reference: str,
    body: IntegrityResponse | None = None,
) -> IntegrityReceipt:
    if body is not None:
        await lock_organization(db, org.id)
    case = await db.scalar(
        select(StudyBusinessEvent)
        .join(
            StudyLearnerProfile,
            StudyLearnerProfile.learner_uuid == StudyBusinessEvent.subject_uuid,
        )
        .where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.event_uuid == reference,
            StudyBusinessEvent.action == CONCERN_RECORDED,
            StudyLearnerProfile.org_id == org.id,
            StudyLearnerProfile.user_id == actor.id,
        )
    )
    if case is None:
        raise HTTPException(404, "Assessment integrity review not found.")
    if await _case_dismissal(db, org.id, reference):
        if body is not None:
            raise HTTPException(409, "The integrity concern is already closed.")
        return IntegrityReceipt(reference=reference, status="dismissed")
    response = await _case_response(db, org.id, reference)
    if body is not None:
        if response is not None and response.reason != body.response.upper():
            raise HTTPException(409, "A different response is already on record.")
        if response is None:
            response = await append_business_event(
                db,
                org_id=org.id,
                actor_user_id=actor.id,
                actor_capacity="learner",
                action=RESPONSE_RECORDED,
                reason_code=body.response.upper(),
                subject_uuid=case.subject_uuid,
                resource_kind="assessment_integrity_case",
                resource_uuid=reference,
                operation_uuid=case.resource_uuid,
                policy_refs=case.policy_refs,
                dedupe_key=f"assessment_integrity_response:{reference}",
            )
            await db.commit()
    return IntegrityReceipt(
        reference=reference,
        status="response_received" if response is not None else "awaiting_review",
    )


async def list_learner_integrity_cases(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
) -> list[IntegrityReceipt]:
    reply = aliased(StudyBusinessEvent)
    dismissal = aliased(StudyBusinessEvent)
    cases = (
        await db.execute(
            select(StudyBusinessEvent.event_uuid, reply.id, dismissal.id)
            .join(
                StudyLearnerProfile,
                StudyLearnerProfile.learner_uuid == StudyBusinessEvent.subject_uuid,
            )
            .outerjoin(
                reply,
                (reply.org_id == org.id)
                & (reply.action == RESPONSE_RECORDED)
                & (reply.resource_uuid == StudyBusinessEvent.event_uuid),
            )
            .outerjoin(
                dismissal,
                (dismissal.org_id == org.id)
                & (dismissal.action == CONCERN_DISMISSED)
                & (dismissal.resource_uuid == StudyBusinessEvent.event_uuid),
            )
            .where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.action == CONCERN_RECORDED,
                StudyLearnerProfile.org_id == org.id,
                StudyLearnerProfile.user_id == actor.id,
            )
            .order_by(StudyBusinessEvent.id.desc())
            .limit(50)
        )
    ).all()
    return [
        IntegrityReceipt(
            reference=reference,
            status="dismissed"
            if dismissal_id
            else ("response_received" if reply_id else "awaiting_review"),
        )
        for reference, reply_id, dismissal_id in cases
    ]
