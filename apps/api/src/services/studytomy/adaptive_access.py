"""Organization policy prerequisites for adaptive learning."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from fastapi import HTTPException, Request
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.courses import Course
from src.db.organization_config import OrganizationConfig
from src.db.organizations import Organization
from src.db.studytomy.family import (
    StudyEligibilityKind,
    StudyLearnerControlMode,
    StudyLearnerProfile,
    StudyLearnerProfileStatus,
)
from src.db.studytomy.governance import StudyPolicyKey
from src.db.users import PublicUser
from src.security.rbac.resource_access import AccessAction, check_resource_access
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_studytomy_staff_action,
)
from src.services.studytomy.learners import (
    _active_policy,
    _decision_read,
    _latest_decisions,
)


class AdaptiveAction(StrEnum):
    DIAGNOSTIC = "diagnostic"
    GUIDED_AI = "guided_ai"
    REPORT = "report"


class AdaptiveDenial(StrEnum):
    DISABLED = "ADAPTIVE_DISABLED"
    POLICY_NOT_APPROVED = "POLICY_NOT_APPROVED"
    RESOURCE_APPROVAL_REQUIRED = "RESOURCE_APPROVAL_REQUIRED"
    DEPENDENT_AUTHORITY_REQUIRED = "DEPENDENT_AUTHORITY_REQUIRED"


class AdaptiveAvailability(BaseModel):
    enabled: bool
    learner_eligible: bool
    allowed_actions: list[AdaptiveAction]
    denials: dict[AdaptiveAction, AdaptiveDenial]


@dataclass(frozen=True)
class AdaptivePolicyReference:
    key: StudyPolicyKey
    uuid: str
    version: int


_LEARNING_POLICIES = (
    StudyPolicyKey.D_01,
    StudyPolicyKey.D_02,
    StudyPolicyKey.D_09,
    StudyPolicyKey.D_12,
    StudyPolicyKey.D_13,
)
_ACTION_POLICIES = {
    AdaptiveAction.DIAGNOSTIC: _LEARNING_POLICIES,
    AdaptiveAction.GUIDED_AI: (*_LEARNING_POLICIES, StudyPolicyKey.D_10),
    AdaptiveAction.REPORT: (*_LEARNING_POLICIES, StudyPolicyKey.D_03),
}


async def authorize_adaptive_course_editor(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
) -> Course:
    course = (
        (
            await db.execute(
                select(Course).where(Course.course_uuid == course_uuid).limit(2)
            )
        )
        .scalars()
        .all()
    )
    if len(course) != 1 or course[0].org_id != org.id:
        raise HTTPException(404, "Course not found.")
    resolved = course[0]
    await authorize_studytomy_staff_action(
        db,
        actor,
        org,
        StudytomyCapabilityName.ADAPTIVE_CURRICULUM_AUTHOR,
        target_scope_kind="course",
        target_resource_uuid=resolved.course_uuid,
        hide_existence_on_denial=True,
    )
    await check_resource_access(
        request, db, actor, resolved.course_uuid, AccessAction.UPDATE
    )
    return resolved


async def current_adaptive_policies(
    db: AsyncSession,
    org: Organization,
    now: datetime,
) -> dict[StudyPolicyKey, AdaptivePolicyReference | None]:
    """Resolve current named-owner decisions once each for this request."""
    keys = dict.fromkeys(key for group in _ACTION_POLICIES.values() for key in group)
    policies = {}
    for key in keys:
        try:
            policy = await _active_policy(db, org, key, now)
        except HTTPException as error:
            if error.status_code != 409:
                raise
            policies[key] = None
        else:
            policies[key] = AdaptivePolicyReference(
                key, policy.policy_uuid, policy.version
            )
    return policies


async def _eligible_learner_control_mode(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    now: datetime,
) -> StudyLearnerControlMode | None:
    profile = (
        await db.execute(
            select(StudyLearnerProfile).where(
                StudyLearnerProfile.org_id == org.id,
                StudyLearnerProfile.user_id == actor.id,
                StudyLearnerProfile.status == StudyLearnerProfileStatus.ACTIVE.value,
                StudyLearnerProfile.control_mode.in_(
                    (
                        StudyLearnerControlMode.DEPENDENT.value,
                        StudyLearnerControlMode.INDEPENDENT.value,
                    )
                ),
            )
        )
    ).scalar_one_or_none()
    if profile is None or not await _is_learning_eligible(db, org, profile, now):
        return None
    return StudyLearnerControlMode(profile.control_mode)


async def _is_learning_eligible(
    db: AsyncSession, org: Organization, profile: StudyLearnerProfile, now: datetime
) -> bool:
    try:
        decisions = await _latest_decisions(db, profile)
    except HTTPException as error:
        if error.status_code != 409:
            raise
        return False
    for decision in decisions:
        if decision.eligibility_kind == StudyEligibilityKind.LEARNING.value:
            return (await _decision_read(db, org, decision, now)).currently_eligible
    return False


async def adaptive_enabled(db: AsyncSession, org: Organization) -> bool:
    configs = (
        (
            await db.execute(
                select(OrganizationConfig.config)
                .where(OrganizationConfig.org_id == org.id)
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    config = configs[0] if len(configs) == 1 else None
    toggles = config.get("admin_toggles") if isinstance(config, dict) else None
    return (
        isinstance(toggles, dict) and toggles.get("studytomy_adaptive_enabled") is True
    )


async def get_adaptive_availability(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
) -> AdaptiveAvailability:
    if not await adaptive_enabled(db, org):
        return AdaptiveAvailability(
            enabled=False,
            learner_eligible=False,
            allowed_actions=[],
            denials=dict.fromkeys(AdaptiveAction, AdaptiveDenial.DISABLED),
        )

    now = datetime.now(UTC)
    policies = await current_adaptive_policies(db, org, now)
    control_mode = None
    if all(policies[key] is not None for key in _LEARNING_POLICIES):
        control_mode = await _eligible_learner_control_mode(db, org, actor, now)
    ready_policy_denial = (
        AdaptiveDenial.DEPENDENT_AUTHORITY_REQUIRED
        if control_mode == StudyLearnerControlMode.DEPENDENT
        else AdaptiveDenial.RESOURCE_APPROVAL_REQUIRED
    )
    # Organization approvals never substitute for the specific learner, course,
    # AI purpose or current recipient authority. Those actions remain closed.
    return AdaptiveAvailability(
        enabled=True,
        learner_eligible=control_mode is not None,
        allowed_actions=[],
        denials={
            action: (
                ready_policy_denial
                if all(policies[key] is not None for key in keys)
                else AdaptiveDenial.POLICY_NOT_APPROVED
            )
            for action, keys in _ACTION_POLICIES.items()
        },
    )
