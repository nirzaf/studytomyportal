"""Read-only course offer readiness evaluator for TASK-008.

Readiness is recomputed from active approved policies and current named owners.
Missing course-review, support-capacity or pilot-acceptance records remain
explicit blockers until their dependent workflows exist; no feature flag or
generic role can turn an unreviewed course into a sale-ready offer.
"""

from datetime import datetime, timezone
from enum import Enum

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.governance import (
    StudyOwnerAssignment,
    StudyOwnerFunction,
    StudyOwnerStatus,
    StudyPolicyApproval,
    StudyPolicyKey,
    StudyPolicyStatus,
    StudyPolicyVersion,
    inspect_study_policy_body,
)
from src.db.user_organizations import UserOrganization
from src.services.studytomy.policies import POLICY_REQUIRED_RESPONSIBILITIES, _as_utc


class StudyOfferKind(str, Enum):
    COURSE = "course"
    MEMBERSHIP = "membership"


class StudyReadinessBlocker(BaseModel):
    code: str
    policy_key: StudyPolicyKey | None = None
    message: str
    next_step: str


class StudyReadinessRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_uuid: str
    offer_kind: StudyOfferKind
    ready_to_sell: bool
    blockers: list[StudyReadinessBlocker]
    evaluated_at: datetime


def _blocker(
    code: str,
    message: str,
    next_step: str,
    policy_key: StudyPolicyKey | None = None,
) -> StudyReadinessBlocker:
    return StudyReadinessBlocker(
        code=code,
        policy_key=policy_key,
        message=message,
        next_step=next_step,
    )


async def _active_owner_assignment(
    db_session: AsyncSession,
    organization: Organization,
    responsibility: StudyOwnerFunction,
    now: datetime,
) -> StudyOwnerAssignment | None:
    result = await db_session.execute(
        select(StudyOwnerAssignment)
        .join(UserOrganization, UserOrganization.user_id == StudyOwnerAssignment.user_id)
        .where(
            StudyOwnerAssignment.org_id == organization.id,
            StudyOwnerAssignment.responsibility == responsibility.value,
            StudyOwnerAssignment.scope_kind == "organization",
            StudyOwnerAssignment.scope_uuid == organization.org_uuid,
            StudyOwnerAssignment.status == StudyOwnerStatus.ACTIVE.value,
            UserOrganization.org_id == organization.id,
        )
        .order_by(StudyOwnerAssignment.created_at.desc())
    )
    for assignment in result.scalars().all():
        if _as_utc(assignment.effective_from) <= now and (
            assignment.effective_to is None or _as_utc(assignment.effective_to) > now
        ):
            return assignment
    return None


async def _active_policy(
    db_session: AsyncSession,
    organization: Organization,
    policy_key: StudyPolicyKey,
    now: datetime,
) -> StudyPolicyVersion | None:
    result = await db_session.execute(
        select(StudyPolicyVersion)
        .where(
            StudyPolicyVersion.org_id == organization.id,
            StudyPolicyVersion.policy_key == policy_key.value,
            StudyPolicyVersion.scope_kind == "organization",
            StudyPolicyVersion.scope_uuid == organization.org_uuid,
            StudyPolicyVersion.status == StudyPolicyStatus.APPROVED.value,
            StudyPolicyVersion.effective_from <= now,
        )
        .order_by(StudyPolicyVersion.version.desc())
    )
    matches = [
        policy
        for policy in result.scalars().all()
        if policy.effective_to is None or _as_utc(policy.effective_to) > now
    ]
    if len(matches) > 1:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POLICY_SCOPE_AMBIGUOUS",
                "message": f"More than one active approved {policy_key.value} policy covers this organization.",
            },
        )
    return matches[0] if matches else None


async def _approval_assignment_is_current(
    db_session: AsyncSession,
    organization: Organization,
    assignment_id: int,
    responsibility: StudyOwnerFunction,
    now: datetime,
) -> bool:
    assignment = await db_session.get(StudyOwnerAssignment, assignment_id)
    if (
        assignment is None
        or assignment.org_id != organization.id
        or assignment.scope_kind != "organization"
        or assignment.scope_uuid != organization.org_uuid
        or assignment.responsibility != responsibility.value
        or assignment.status != StudyOwnerStatus.ACTIVE.value
        or _as_utc(assignment.effective_from) > now
        or (assignment.effective_to is not None and _as_utc(assignment.effective_to) <= now)
    ):
        return False
    membership = await db_session.execute(
        select(UserOrganization.id).where(
            UserOrganization.org_id == organization.id,
            UserOrganization.user_id == assignment.user_id,
        )
    )
    return membership.scalar_one_or_none() is not None


async def evaluate_course_offer_readiness(
    db_session: AsyncSession,
    organization: Organization,
    course_uuid: str,
    offer_kind: StudyOfferKind = StudyOfferKind.COURSE,
) -> StudyReadinessRead:
    """Recompute sale blockers for a course offer without changing commitments."""
    now = datetime.now(timezone.utc)
    course_result = await db_session.execute(
        select(Course).where(
            Course.course_uuid == course_uuid,
            Course.org_id == organization.id,
        )
    )
    course = course_result.scalars().first()
    if course is None:
        raise HTTPException(status_code=404, detail="Course not found")

    blockers: list[StudyReadinessBlocker] = []
    if not course.published:
        blockers.append(
            _blocker(
                "COURSE_NOT_PUBLISHED",
                "This course is not published for learner access.",
                "Publish the reviewed course before evaluating it for sale.",
            )
        )

    required_decisions = [
        StudyPolicyKey.D_01,
        StudyPolicyKey.D_02,
        StudyPolicyKey.D_04,
        StudyPolicyKey.D_05,
        StudyPolicyKey.D_15,
        StudyPolicyKey.D_17,
        StudyPolicyKey.D_18,
    ]
    if offer_kind == StudyOfferKind.MEMBERSHIP:
        required_decisions.append(StudyPolicyKey.D_06)

    for policy_key in required_decisions:
        policy = await _active_policy(db_session, organization, policy_key, now)
        required_responsibilities = POLICY_REQUIRED_RESPONSIBILITIES[policy_key]
        for responsibility in required_responsibilities:
            owner = await _active_owner_assignment(
                db_session,
                organization,
                responsibility,
                now,
            )
            if owner is None:
                blockers.append(
                    _blocker(
                        "SERVICE_OWNER_MISSING",
                        f"No active {responsibility.value.replace('_', ' ')} owner is assigned.",
                        "Assign an accountable organization member for this responsibility.",
                        policy_key,
                    )
                )

        if policy is None:
            blockers.append(
                _blocker(
                    "DECISION_NOT_APPROVED",
                    f"Decision {policy_key.value} has no active approved version.",
                    "Record and approve a scoped policy version with named responsible owners.",
                    policy_key,
                )
            )
            continue

        policy_body, body_blockers = inspect_study_policy_body(policy_key, policy.body)
        if policy_body is None:
            blockers.append(
                _blocker(
                    "POLICY_BODY_INVALID",
                    f"Decision {policy_key.value} does not match its typed decision contract.",
                    "Propose a valid policy version and obtain the required named-owner approvals.",
                    policy_key,
                )
            )
            continue
        if any(body_blockers.values()):
            blockers.append(
                _blocker(
                    "POLICY_BODY_INCOMPLETE",
                    f"Decision {policy_key.value} has missing decision areas or evidence.",
                    "Propose a complete policy version and obtain the required named-owner approvals.",
                    policy_key,
                )
            )
            continue

        for responsibility in required_responsibilities:
            approval_result = await db_session.execute(
                select(StudyPolicyApproval).where(
                    StudyPolicyApproval.org_id == organization.id,
                    StudyPolicyApproval.policy_version_id == policy.id,
                    StudyPolicyApproval.responsibility == responsibility.value,
                )
            )
            approval = approval_result.scalars().first()
            if approval is None:
                blockers.append(
                    _blocker(
                        "POLICY_APPROVAL_EVIDENCE_MISSING",
                        f"Decision {policy_key.value} is missing {responsibility.value.replace('_', ' ')} approval evidence.",
                        "Record the required named-owner approval for this policy version.",
                        policy_key,
                    )
                )
            elif not await _approval_assignment_is_current(
                db_session,
                organization,
                approval.owner_assignment_id,
                responsibility,
                now,
            ):
                blockers.append(
                    _blocker(
                        "POLICY_APPROVER_NO_LONGER_ACTIVE",
                        f"The {responsibility.value.replace('_', ' ')} approval is no longer backed by a current owner assignment.",
                        "Reassign the responsibility and obtain approval for a current policy version.",
                        policy_key,
                    )
                )

    # TASK-036 has not created course review evidence yet. Keep every course
    # unavailable for sale until approval is bound to the exact content revision.
    blockers.append(
        _blocker(
            "COURSE_REVIEW_EVIDENCE_MISSING",
            "No verified content and rights review is bound to this course revision.",
            "Complete the version-bound course review before marking the offer ready.",
        )
    )
    # D-04 capacity and D-17 acceptance evidence need operational records that
    # are introduced by later staffing and pilot-measure tasks.
    blockers.append(
        _blocker(
            "SUPPORT_CAPACITY_EVIDENCE_MISSING",
            "No verified support-capacity record is available for this course offer.",
            "Record staffed capacity and the support route before making a service promise.",
            StudyPolicyKey.D_04,
        )
    )
    blockers.append(
        _blocker(
            "PILOT_ACCEPTANCE_EVIDENCE_MISSING",
            "No reconciled pilot acceptance evidence is available for this offer.",
            "Record the approved pilot measures and acceptance evidence before sale.",
            StudyPolicyKey.D_17,
        )
    )

    return StudyReadinessRead(
        course_uuid=course.course_uuid,
        offer_kind=offer_kind,
        ready_to_sell=not blockers,
        blockers=blockers,
        evaluated_at=now,
    )
