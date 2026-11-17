"""Policy-bound, versioned guardian authority requests (TASK-011).

Requests carry no permissions. A separate assigned family-services reviewer
must accept an explicit subset of the requested scopes, with a bounded period
and current D-03/D-12/D-13 policy references, before the resolver can return a
grant.
"""

import re
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.family import (
    StudyFamilyReviewQueueItem,
    StudyLearnerControlMode,
    StudyLearnerProfile,
    StudyLearnerProfileStatus,
    StudyRelationship,
    StudyRelationshipDecisionCreate,
    StudyRelationshipDecisionOutcome,
    StudyRelationshipRead,
    StudyRelationshipRequestCreate,
    StudyRelationshipRestrictionAction,
    StudyRelationshipRestrictionCreate,
    StudyRelationshipReviewRead,
    StudyRelationshipStatus,
    StudyRelationshipType,
)
from src.db.studytomy.governance import (
    StudyOwnerAssignment,
    StudyOwnerFunction,
    StudyOwnerStatus,
    StudyPolicyBody,
    StudyPolicyKey,
    StudyPolicyStatus,
    StudyPolicyVersion,
    StudyStaffScope,
    inspect_study_policy_body,
)
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_studytomy_staff_action,
)
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.idempotency import claim_idempotency, complete_idempotency
from src.services.studytomy.policies import _as_utc, lock_organization, lock_users
from src.services.studytomy.privacy import invalidate_protected_studytomy_user_ids

_RELATIONSHIP_POLICY_KEYS = (
    StudyPolicyKey.D_03,
    StudyPolicyKey.D_12,
    StudyPolicyKey.D_13,
)
_ACTIVE_OR_REVIEW_STATUSES = (
    StudyRelationshipStatus.PENDING.value,
    StudyRelationshipStatus.ACCEPTED.value,
    StudyRelationshipStatus.RESTRICTED.value,
    StudyRelationshipStatus.DISPUTED.value,
)


def _policy_problem(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": code, "message": message})


def _policy_scope_codes(value: object) -> set[str]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 64
        or any(
            not isinstance(scope, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", scope) is None
            for scope in value
        )
        or len(set(value)) != len(value)
    ):
        raise _policy_problem(
            "POLICY_SCOPE_INVALID",
            "The approved D-12 parent report scope must be a list of unique scope codes.",
        )
    return set(value)


async def _current_relationship_policies(
    db_session: AsyncSession,
    organization: Organization,
    now: datetime,
) -> tuple[list[StudyPolicyVersion], set[str]]:
    """Resolve current, complete policy versions and the explicitly approved scopes."""
    policies: list[StudyPolicyVersion] = []
    bodies: dict[StudyPolicyKey, StudyPolicyBody] = {}
    for policy_key in _RELATIONSHIP_POLICY_KEYS:
        result = await db_session.execute(
            select(StudyPolicyVersion)
            .where(
                StudyPolicyVersion.org_id == organization.id,
                StudyPolicyVersion.policy_key == policy_key.value,
                StudyPolicyVersion.scope_kind == "organization",
                StudyPolicyVersion.scope_uuid == organization.org_uuid,
                StudyPolicyVersion.status == StudyPolicyStatus.APPROVED.value,
            )
            .order_by(StudyPolicyVersion.version.desc())
        )
        matches = [
            policy
            for policy in result.scalars().all()
            if _as_utc(policy.effective_from) <= now
            and (policy.effective_to is None or _as_utc(policy.effective_to) > now)
        ]
        if len(matches) > 1:
            raise _policy_problem(
                "POLICY_SCOPE_AMBIGUOUS",
                f"More than one active approved {policy_key.value} policy covers this organization.",
            )
        if not matches:
            raise _policy_problem(
                "POLICY_NOT_APPROVED",
                f"An active approved {policy_key.value} decision is required.",
            )
        policy = matches[0]
        body, blockers = inspect_study_policy_body(policy_key, policy.body)
        if body is None or any(blockers.values()):
            raise _policy_problem(
                "POLICY_BODY_INVALID",
                f"The approved {policy_key.value} policy body is incomplete or invalid.",
            )
        policies.append(policy)
        bodies[policy_key] = body

    required_decisions = (
        (StudyPolicyKey.D_03, "guardian_authority"),
        (StudyPolicyKey.D_12, "information_uses"),
        (StudyPolicyKey.D_12, "consent"),
        (StudyPolicyKey.D_12, "parent_report_scope"),
        (StudyPolicyKey.D_13, "retention"),
        (StudyPolicyKey.D_13, "record_access"),
    )
    if any(bodies[key].specified_value(area) is None for key, area in required_decisions):
        raise _policy_problem(
            "POLICY_DECISION_UNRESOLVED",
            "Guardian authority requires explicit D-03, D-12 and D-13 decisions for this use.",
        )
    allowed_scopes = _policy_scope_codes(
        bodies[StudyPolicyKey.D_12].specified_value("parent_report_scope")
    )
    return policies, allowed_scopes


async def _current_restriction_policies(
    db_session: AsyncSession,
    organization: Organization,
    now: datetime,
) -> tuple[list[StudyPolicyVersion], set[str]]:
    """Resolve TASK-012's additional adult-contact/dispute decision gate."""
    relationship_policies, allowed_scopes = await _current_relationship_policies(
        db_session, organization, now
    )
    result = await db_session.execute(
        select(StudyPolicyVersion)
        .where(
            StudyPolicyVersion.org_id == organization.id,
            StudyPolicyVersion.policy_key == StudyPolicyKey.D_11.value,
            StudyPolicyVersion.scope_kind == "organization",
            StudyPolicyVersion.scope_uuid == organization.org_uuid,
            StudyPolicyVersion.status == StudyPolicyStatus.APPROVED.value,
        )
        .order_by(StudyPolicyVersion.version.desc())
    )
    matches = [
        policy
        for policy in result.scalars().all()
        if _as_utc(policy.effective_from) <= now
        and (policy.effective_to is None or _as_utc(policy.effective_to) > now)
    ]
    if len(matches) > 1:
        raise _policy_problem(
            "POLICY_SCOPE_AMBIGUOUS",
            "More than one active approved D-11 policy covers this organization.",
        )
    if not matches:
        raise _policy_problem(
            "POLICY_NOT_APPROVED",
            "An active approved D-11 decision is required for this relationship transition.",
        )
    policy = matches[0]
    body, blockers = inspect_study_policy_body(StudyPolicyKey.D_11, policy.body)
    if body is None or any(blockers.values()):
        raise _policy_problem(
            "POLICY_BODY_INVALID",
            "The approved D-11 policy body is incomplete or invalid.",
        )
    if body.specified_value("adult_contact_boundaries") is None:
        raise _policy_problem(
            "POLICY_DECISION_UNRESOLVED",
            "Relationship restriction requires an explicit D-11 adult-contact decision.",
        )
    return [relationship_policies[0], policy, *relationship_policies[1:]], allowed_scopes


def _relationship_read(
    relationship: StudyRelationship,
    profile: StudyLearnerProfile,
) -> StudyRelationshipRead:
    return StudyRelationshipRead(
        relationship_uuid=relationship.relationship_uuid,
        learner_uuid=profile.learner_uuid,
        relationship_type=StudyRelationshipType(relationship.relationship_type),
        status=StudyRelationshipStatus(relationship.status),
        requested_scopes=list(relationship.requested_scopes),
        approved_scopes=list(relationship.approved_scopes),
        valid_from=relationship.valid_from,
        valid_to=relationship.valid_to,
        authority_version=relationship.authority_version,
        created_at=relationship.created_at,
        updated_at=relationship.updated_at,
        decided_at=relationship.decided_at,
    )


async def _locked_profile(
    db_session: AsyncSession,
    organization: Organization,
    learner_uuid: str,
) -> StudyLearnerProfile:
    result = await db_session.execute(
        select(StudyLearnerProfile)
        .where(
            StudyLearnerProfile.org_id == organization.id,
            StudyLearnerProfile.learner_uuid == learner_uuid,
        )
        .with_for_update()
    )
    profile = result.scalars().first()
    if profile is None or profile.status == StudyLearnerProfileStatus.CLOSED.value:
        raise HTTPException(status_code=404, detail="Learner profile not found")
    return profile


async def _require_current_membership(
    db_session: AsyncSession,
    organization: Organization,
    user_id: int,
) -> bool:
    result = await db_session.execute(
        select(UserOrganization.id).where(
            UserOrganization.org_id == organization.id,
            UserOrganization.user_id == user_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def _reviewer_owner_assignment(
    db_session: AsyncSession,
    organization: Organization,
    reviewer_user_id: int,
    learner_uuid: str,
    now: datetime,
) -> StudyOwnerAssignment:
    capabilities = (
        StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW.value,
        f"studytomy.{StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW.value}",
    )
    result = await db_session.execute(
        select(StudyOwnerAssignment)
        .join(StudyStaffScope, StudyStaffScope.assignment_id == StudyOwnerAssignment.id)
        .where(
            StudyOwnerAssignment.org_id == organization.id,
            StudyOwnerAssignment.user_id == reviewer_user_id,
            StudyOwnerAssignment.responsibility == StudyOwnerFunction.FAMILY_SERVICES.value,
            StudyOwnerAssignment.scope_kind == "organization",
            StudyOwnerAssignment.scope_uuid == organization.org_uuid,
            StudyOwnerAssignment.status == StudyOwnerStatus.ACTIVE.value,
            StudyStaffScope.org_id == organization.id,
            StudyStaffScope.capability.in_(capabilities),
            StudyStaffScope.scope_kind == "learner",
            StudyStaffScope.scope_uuid == learner_uuid,
            StudyStaffScope.is_exclusion.is_(False),
            StudyStaffScope.status == "active",
        )
        .order_by(StudyOwnerAssignment.created_at.desc(), StudyOwnerAssignment.id.desc())
    )
    for owner in result.scalars().all():
        if _as_utc(owner.effective_from) <= now and (
            owner.effective_to is None or _as_utc(owner.effective_to) > now
        ):
            return owner
    raise _policy_problem(
        "OWNER_ASSIGNMENT_CHANGED",
        "The assigned family-services reviewer is no longer active for this learner.",
    )


async def create_relationship_request(
    db_session: AsyncSession,
    organization: Organization,
    adult: PublicUser,
    learner_uuid: str,
    request: StudyRelationshipRequestCreate,
    idempotency_key: str,
) -> StudyRelationshipRead:
    """Record a pending claim without assigning any relationship authority."""
    await lock_organization(db_session, int(organization.id))
    profile = await _locked_profile(db_session, organization, learner_uuid)
    if int(profile.user_id) == int(adult.id):
        raise HTTPException(status_code=422, detail="A learner cannot request guardian authority over themself")
    if not await _require_current_membership(db_session, organization, int(adult.id)):
        raise HTTPException(status_code=404, detail="Learner profile not found")
    if not await _require_current_membership(db_session, organization, int(profile.user_id)):
        raise HTTPException(status_code=404, detail="Learner profile not found")

    now = datetime.now(timezone.utc)
    policies, allowed_scopes = await _current_relationship_policies(db_session, organization, now)
    requested_scopes = set(request.requested_scopes)
    if not requested_scopes.issubset(allowed_scopes):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SCOPE_NOT_APPROVED",
                "message": "One or more requested scopes are not included in the approved D-12 policy.",
            },
        )

    await lock_users(db_session, [int(adult.id), int(profile.user_id)])
    replay = await claim_idempotency(
        db_session,
        org_id=int(organization.id),
        actor_user_id=int(adult.id),
        operation="studytomy.relationship.request",
        idempotency_key=idempotency_key,
        request_payload={
            "learner_uuid": learner_uuid,
            "request": request.model_dump(mode="json"),
        },
    )
    if replay is not None:
        existing_result = await db_session.execute(
            select(StudyRelationship).where(
                StudyRelationship.org_id == organization.id,
                StudyRelationship.learner_profile_id == profile.id,
                StudyRelationship.adult_user_id == adult.id,
                StudyRelationship.relationship_uuid == replay.resource_uuid,
            )
        )
        existing = existing_result.scalars().first()
        if existing is None:
            raise HTTPException(status_code=409, detail="The idempotent relationship request is unavailable")
        return _relationship_read(existing, profile)

    existing_result = await db_session.execute(
        select(StudyRelationship)
        .where(
            StudyRelationship.org_id == organization.id,
            StudyRelationship.learner_profile_id == profile.id,
            StudyRelationship.adult_user_id == adult.id,
            StudyRelationship.status.in_(_ACTIVE_OR_REVIEW_STATUSES),
        )
        .with_for_update()
    )
    for existing in existing_result.scalars().all():
        if existing.status in {
            StudyRelationshipStatus.PENDING.value,
            StudyRelationshipStatus.RESTRICTED.value,
            StudyRelationshipStatus.DISPUTED.value,
        }:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "RELATIONSHIP_REVIEW_ALREADY_OPEN",
                    "message": "A current relationship review must be resolved before a new request.",
                },
            )
        if existing.valid_to is None or _as_utc(existing.valid_to) > now:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "RELATIONSHIP_ALREADY_ACTIVE",
                    "message": "A current relationship must be reviewed before another request is opened.",
                },
            )

    relationship = StudyRelationship(
        org_id=int(organization.id),
        learner_profile_id=int(profile.id),
        adult_user_id=int(adult.id),
        requested_by_user_id=int(adult.id),
        relationship_type=StudyRelationshipType.GUARDIAN.value,
        requested_scopes=request.requested_scopes,
        approved_scopes=[],
        status=StudyRelationshipStatus.PENDING.value,
        request_evidence_reference=request.evidence_reference,
        request_policy_version_ids=[int(policy.id) for policy in policies],
        decision_policy_version_ids=None,
        authority_version=1,
        created_at=now,
        updated_at=now,
    )
    db_session.add(relationship)
    await db_session.flush()
    await append_business_event(
        db_session,
        org_id=int(organization.id),
        actor_user_id=int(adult.id),
        actor_capacity="relationship_requester",
        subject_uuid=profile.learner_uuid,
        resource_kind="relationship",
        resource_uuid=relationship.relationship_uuid,
        action="studytomy.relationship.requested",
        reason_code="RELATIONSHIP_REQUESTED",
        after_state={
            "status": relationship.status,
            "relationship_type": relationship.relationship_type,
            "requested_scopes": relationship.requested_scopes,
            "authority_version": relationship.authority_version,
        },
        policy_refs=relationship.request_policy_version_ids,
        dedupe_key=f"studytomy:relationship:{relationship.relationship_uuid}:requested",
    )
    await complete_idempotency(
        db_session,
        org_id=int(organization.id),
        actor_user_id=int(adult.id),
        operation="studytomy.relationship.request",
        idempotency_key=idempotency_key,
        resource_kind="relationship",
        resource_uuid=relationship.relationship_uuid,
        response_status=202,
        response_projection={"relationship_uuid": relationship.relationship_uuid},
    )
    await db_session.commit()
    await db_session.refresh(relationship)
    return _relationship_read(relationship, profile)


async def list_my_relationship_requests(
    db_session: AsyncSession,
    organization: Organization,
    adult: PublicUser,
) -> list[StudyRelationshipRead]:
    if not await _require_current_membership(db_session, organization, int(adult.id)):
        raise HTTPException(status_code=404, detail="Organization not found")
    result = await db_session.execute(
        select(StudyRelationship)
        .where(
            StudyRelationship.org_id == organization.id,
            StudyRelationship.adult_user_id == adult.id,
        )
        .order_by(StudyRelationship.created_at.desc(), StudyRelationship.id.desc())
        .limit(100)
    )
    relationships = list(result.scalars().all())
    profiles_by_id: dict[int, StudyLearnerProfile] = {}
    reads: list[StudyRelationshipRead] = []
    for relationship in relationships:
        profile = profiles_by_id.get(int(relationship.learner_profile_id))
        if profile is None:
            profile = await db_session.get(StudyLearnerProfile, relationship.learner_profile_id)
            if profile is None or profile.org_id != organization.id:
                continue
            profiles_by_id[int(profile.id)] = profile
        reads.append(_relationship_read(relationship, profile))
    return reads


async def list_family_review_queue(
    db_session: AsyncSession,
    organization: Organization,
    reviewer: PublicUser,
) -> list[StudyFamilyReviewQueueItem]:
    """List only learners covered by the reviewer's exact active staff scopes."""
    now = datetime.now(timezone.utc)
    relationship_policies, allowed_scopes = await _current_relationship_policies(
        db_session, organization, now
    )
    current_policy_ids = [int(policy.id) for policy in relationship_policies]
    from src.services.studytomy.learners import _current_policies

    support_policies = await _current_policies(
        db_session, organization, (StudyPolicyKey.D_04,), now
    )
    support_body, support_blockers = inspect_study_policy_body(
        StudyPolicyKey.D_04, support_policies[0].body
    )
    required_support_areas = (
        "support_routes",
        "monitored_hours",
        "response_targets",
        "staffing",
    )
    if (
        support_body is None
        or any(support_blockers.values())
        or any(support_body.specified_value(area) is None for area in required_support_areas)
    ):
        raise _policy_problem(
            "FAMILY_REVIEW_SUPPORT_POLICY_INCOMPLETE",
            "The family review workspace requires explicit current D-04 support and staffing decisions.",
        )
    if not await _require_current_membership(db_session, organization, int(reviewer.id)):
        raise HTTPException(status_code=404, detail="Organization not found")

    capability_alias = f"studytomy.{StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW.value}"
    status_filter = or_(
        StudyRelationship.status == StudyRelationshipStatus.PENDING.value,
        StudyRelationship.status.in_(
            (
                StudyRelationshipStatus.DENIED.value,
                StudyRelationshipStatus.RESTRICTED.value,
                StudyRelationshipStatus.DISPUTED.value,
            )
        ),
        and_(
            StudyRelationship.status == StudyRelationshipStatus.ACCEPTED.value,
            StudyRelationship.valid_from <= now,
            StudyRelationship.valid_to > now,
        ),
    )
    query = (
        select(StudyRelationship, StudyLearnerProfile)
        .join(
            StudyLearnerProfile,
            StudyLearnerProfile.id == StudyRelationship.learner_profile_id,
        )
        .join(
            StudyStaffScope,
            and_(
                StudyStaffScope.org_id == organization.id,
                StudyStaffScope.scope_kind == "learner",
                StudyStaffScope.scope_uuid == StudyLearnerProfile.learner_uuid,
                StudyStaffScope.capability.in_(
                    (
                        StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW.value,
                        capability_alias,
                    )
                ),
            ),
        )
        .join(StudyOwnerAssignment, StudyOwnerAssignment.id == StudyStaffScope.assignment_id)
        .where(
            StudyRelationship.org_id == organization.id,
            StudyLearnerProfile.org_id == organization.id,
            StudyOwnerAssignment.org_id == organization.id,
            StudyOwnerAssignment.user_id == reviewer.id,
            StudyOwnerAssignment.responsibility == StudyOwnerFunction.FAMILY_SERVICES.value,
            StudyOwnerAssignment.scope_kind == "organization",
            StudyOwnerAssignment.scope_uuid == organization.org_uuid,
            StudyOwnerAssignment.status == StudyOwnerStatus.ACTIVE.value,
            StudyOwnerAssignment.effective_from <= now,
            StudyStaffScope.status == "active",
            StudyStaffScope.is_exclusion.is_(False),
            status_filter,
        )
        .order_by(StudyRelationship.created_at.desc(), StudyRelationship.id.desc())
        .limit(300)
    )
    rows = (await db_session.execute(query)).all()
    result: list[StudyFamilyReviewQueueItem] = []
    seen: set[tuple[int, int]] = set()
    for relationship, profile in rows:
        if (
            relationship.adult_user_id == reviewer.id
            or profile.user_id == reviewer.id
            or (int(profile.id), int(relationship.id)) in seen
        ):
            continue
        if relationship.status == StudyRelationshipStatus.ACCEPTED.value and (
            profile.status != StudyLearnerProfileStatus.ACTIVE.value
            or profile.control_mode != StudyLearnerControlMode.DEPENDENT.value
            or relationship.decision_policy_version_ids != current_policy_ids
            or not set(relationship.approved_scopes).issubset(allowed_scopes)
            or relationship.valid_from is None
            or relationship.valid_to is None
            or _as_utc(relationship.valid_from) > now
            or _as_utc(relationship.valid_to) <= now
        ):
            continue
        try:
            await authorize_studytomy_staff_action(
                db_session,
                reviewer,
                organization,
                StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW,
                target_resource_uuid=profile.learner_uuid,
                target_scope_kind="learner",
                subject_user_id=int(profile.user_id),
                excluded_user_ids={int(profile.user_id), int(relationship.adult_user_id)},
                access_version=profile.access_version,
                hide_existence_on_denial=True,
                now=now,
            )
        except HTTPException as error:
            if error.status_code in (403, 404):
                continue
            raise
        seen.add((int(profile.id), int(relationship.id)))
        result.append(
            StudyFamilyReviewQueueItem(
                learner_uuid=profile.learner_uuid,
                relationship_uuid=relationship.relationship_uuid,
                status=StudyRelationshipStatus(relationship.status),
                requested_scopes=list(relationship.requested_scopes),
                approved_scopes=list(relationship.approved_scopes),
                authority_version=relationship.authority_version,
                created_at=relationship.created_at,
                updated_at=relationship.updated_at,
            )
        )
        if len(result) >= 100:
            break
    return result


async def get_relationship_for_review(
    db_session: AsyncSession,
    organization: Organization,
    reviewer: PublicUser,
    learner_uuid: str,
    relationship_uuid: str,
) -> StudyRelationshipReviewRead:
    profile_result = await db_session.execute(
        select(StudyLearnerProfile).where(
            StudyLearnerProfile.org_id == organization.id,
            StudyLearnerProfile.learner_uuid == learner_uuid,
        )
    )
    profile = profile_result.scalars().first()
    if profile is None:
        raise HTTPException(status_code=404, detail="Relationship request not found")
    relationship_result = await db_session.execute(
        select(StudyRelationship).where(
            StudyRelationship.org_id == organization.id,
            StudyRelationship.learner_profile_id == profile.id,
            StudyRelationship.relationship_uuid == relationship_uuid,
        )
    )
    relationship = relationship_result.scalars().first()
    if relationship is None:
        raise HTTPException(status_code=404, detail="Relationship request not found")

    await authorize_studytomy_staff_action(
        db_session,
        reviewer,
        organization,
        StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW,
        target_resource_uuid=profile.learner_uuid,
        target_scope_kind="learner",
        subject_user_id=int(profile.user_id),
        excluded_user_ids={int(profile.user_id), int(relationship.adult_user_id)},
        access_version=profile.access_version,
        hide_existence_on_denial=True,
    )
    now = datetime.now(timezone.utc)
    if relationship.status in {
        StudyRelationshipStatus.RESTRICTED.value,
        StudyRelationshipStatus.DISPUTED.value,
        StudyRelationshipStatus.REVOKED.value,
    }:
        await _current_restriction_policies(db_session, organization, now)
    else:
        await _current_relationship_policies(db_session, organization, now)
    await lock_users(
        db_session,
        [int(reviewer.id), int(profile.user_id), int(relationship.adult_user_id)],
    )
    adult_user = await db_session.get(User, relationship.adult_user_id)
    if adult_user is None:
        raise HTTPException(status_code=404, detail="Relationship request not found")
    return StudyRelationshipReviewRead(
        **_relationship_read(relationship, profile).model_dump(),
        adult_user_uuid=adult_user.user_uuid,
        request_evidence_reference=relationship.request_evidence_reference,
        request_policy_version_ids=relationship.request_policy_version_ids,
        decision_policy_version_ids=relationship.decision_policy_version_ids,
        decision_evidence_reference=relationship.decision_evidence_reference,
        decision_reason_code=relationship.decision_reason_code,
    )


async def decide_relationship_request(
    db_session: AsyncSession,
    organization: Organization,
    reviewer: PublicUser,
    learner_uuid: str,
    relationship_uuid: str,
    request: StudyRelationshipDecisionCreate,
    idempotency_key: str,
) -> StudyRelationshipRead:
    """Accept or deny a pending request under exact scope, policy and version checks."""
    await lock_organization(db_session, int(organization.id))
    profile = await _locked_profile(db_session, organization, learner_uuid)
    relationship_result = await db_session.execute(
        select(StudyRelationship)
        .where(
            StudyRelationship.org_id == organization.id,
            StudyRelationship.learner_profile_id == profile.id,
            StudyRelationship.relationship_uuid == relationship_uuid,
        )
        .with_for_update()
    )
    relationship = relationship_result.scalars().first()
    if relationship is None:
        raise HTTPException(status_code=404, detail="Relationship request not found")

    now = datetime.now(timezone.utc)
    await authorize_studytomy_staff_action(
        db_session,
        reviewer,
        organization,
        StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW,
        target_resource_uuid=profile.learner_uuid,
        target_scope_kind="learner",
        subject_user_id=int(profile.user_id),
        excluded_user_ids={int(profile.user_id), int(relationship.adult_user_id)},
        access_version=profile.access_version,
        hide_existence_on_denial=True,
        now=now,
    )
    policies, allowed_scopes = await _current_relationship_policies(
        db_session, organization, now
    )
    current_policy_ids = [int(policy.id) for policy in policies]
    if relationship.request_policy_version_ids != current_policy_ids:
        raise _policy_problem(
            "RELATIONSHIP_POLICY_STALE",
            "The request must be resubmitted under the current authority, information-use and retention policies.",
        )

    await lock_users(
        db_session,
        [int(reviewer.id), int(profile.user_id), int(relationship.adult_user_id)],
    )
    if not await _require_current_membership(
        db_session, organization, int(relationship.adult_user_id)
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "RELATIONSHIP_ADULT_MEMBERSHIP_ENDED",
                "message": "The relationship requester is no longer an organization member.",
            },
        )

    replay = await claim_idempotency(
        db_session,
        org_id=int(organization.id),
        actor_user_id=int(reviewer.id),
        operation="studytomy.relationship.decide",
        idempotency_key=idempotency_key,
        request_payload={
            "learner_uuid": learner_uuid,
            "relationship_uuid": relationship_uuid,
            "decision": request.model_dump(mode="json"),
        },
    )
    if replay is not None:
        if replay.resource_uuid != relationship.relationship_uuid:
            raise HTTPException(status_code=409, detail="The idempotent relationship decision is unavailable")
        return _relationship_read(relationship, profile)

    if request.expected_version != relationship.authority_version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STALE_AUTHORITY_VERSION",
                "message": "The relationship request changed. Reload it before recording a decision.",
                "current_version": relationship.authority_version,
            },
        )
    if relationship.status != StudyRelationshipStatus.PENDING.value:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "RELATIONSHIP_NOT_PENDING",
                "message": "Only a pending relationship request can receive its initial decision.",
            },
        )
    if (
        request.outcome == StudyRelationshipDecisionOutcome.ACCEPT
        and (
            not set(request.approved_scopes).issubset(set(relationship.requested_scopes))
            or not set(request.approved_scopes).issubset(allowed_scopes)
        )
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SCOPE_NOT_REQUESTED_OR_APPROVED",
                "message": "Approved scopes must be both requested and included in current D-12 policy.",
            },
        )
    if (
        request.outcome == StudyRelationshipDecisionOutcome.ACCEPT
        and profile.control_mode != StudyLearnerControlMode.DEPENDENT.value
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LEARNER_CONTROL_MODE_REVIEW_REQUIRED",
                "message": "Guardian authority cannot be accepted while the learner's control mode is independent or unresolved.",
            },
        )

    if request.outcome == StudyRelationshipDecisionOutcome.ACCEPT:
        assert request.valid_from is not None and request.valid_to is not None
        if _as_utc(request.valid_from) < now or _as_utc(request.valid_to) <= now:
            raise HTTPException(
                status_code=422,
                detail="Accepted authority must begin now or later and end in the future.",
            )
        overlaps = await db_session.execute(
            select(StudyRelationship)
            .where(
                StudyRelationship.org_id == organization.id,
                StudyRelationship.learner_profile_id == profile.id,
                StudyRelationship.adult_user_id == relationship.adult_user_id,
                StudyRelationship.id != relationship.id,
                StudyRelationship.status.in_(
                    (
                        StudyRelationshipStatus.ACCEPTED.value,
                        StudyRelationshipStatus.RESTRICTED.value,
                        StudyRelationshipStatus.DISPUTED.value,
                    )
                ),
            )
            .with_for_update()
        )
        new_start = _as_utc(request.valid_from)
        new_end = _as_utc(request.valid_to)
        for current in overlaps.scalars().all():
            if current.status in {
                StudyRelationshipStatus.RESTRICTED.value,
                StudyRelationshipStatus.DISPUTED.value,
            }:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "RELATIONSHIP_REVIEW_REQUIRED",
                        "message": "An existing relationship requires a separate review before another can be accepted.",
                    },
                )
            current_start = _as_utc(current.valid_from) if current.valid_from else None
            current_end = _as_utc(current.valid_to) if current.valid_to else None
            if current_start is None or current_end is None or (
                new_start < current_end and current_start < new_end
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "OVERLAPPING_GUARDIAN_AUTHORITY",
                        "message": "An equivalent accepted relationship already covers part of this period.",
                    },
                )

    owner_assignment = await _reviewer_owner_assignment(
        db_session,
        organization,
        int(reviewer.id),
        profile.learner_uuid,
        now,
    )
    before_state = {
        "status": relationship.status,
        "approved_scopes": relationship.approved_scopes,
        "authority_version": relationship.authority_version,
        "learner_access_version": profile.access_version,
    }
    if request.outcome == StudyRelationshipDecisionOutcome.ACCEPT:
        relationship.status = StudyRelationshipStatus.ACCEPTED.value
        relationship.approved_scopes = request.approved_scopes
        relationship.valid_from = request.valid_from
        relationship.valid_to = request.valid_to
    else:
        relationship.status = StudyRelationshipStatus.DENIED.value
        relationship.approved_scopes = []
        relationship.valid_from = None
        relationship.valid_to = None
    relationship.decision_policy_version_ids = current_policy_ids
    relationship.decided_by_user_id = int(reviewer.id)
    relationship.decision_owner_assignment_id = int(owner_assignment.id)
    relationship.decision_evidence_reference = request.evidence_reference
    relationship.decision_reason_code = request.reason_code
    relationship.decided_at = now
    relationship.authority_version += 1
    relationship.updated_at = now
    profile.access_version += 1
    profile.updated_at = now
    db_session.add(relationship)
    db_session.add(profile)
    await db_session.flush()
    invalidate_protected_studytomy_user_ids(db_session)
    await append_business_event(
        db_session,
        org_id=int(organization.id),
        actor_user_id=int(reviewer.id),
        actor_capacity=StudyOwnerFunction.FAMILY_SERVICES.value,
        subject_uuid=profile.learner_uuid,
        resource_kind="relationship",
        resource_uuid=relationship.relationship_uuid,
        action="studytomy.relationship.decided",
        reason_code=(
            "RELATIONSHIP_AUTHORITY_ACCEPTED"
            if request.outcome == StudyRelationshipDecisionOutcome.ACCEPT
            else "RELATIONSHIP_AUTHORITY_DENIED"
        ),
        before_state=before_state,
        after_state={
            "status": relationship.status,
            "approved_scopes": relationship.approved_scopes,
            "valid_from": relationship.valid_from.isoformat() if relationship.valid_from else None,
            "valid_to": relationship.valid_to.isoformat() if relationship.valid_to else None,
            "authority_version": relationship.authority_version,
            "learner_access_version": profile.access_version,
        },
        policy_refs=current_policy_ids,
        dedupe_key=f"studytomy:relationship:{relationship.relationship_uuid}:v{relationship.authority_version}",
    )
    await complete_idempotency(
        db_session,
        org_id=int(organization.id),
        actor_user_id=int(reviewer.id),
        operation="studytomy.relationship.decide",
        idempotency_key=idempotency_key,
        resource_kind="relationship",
        resource_uuid=relationship.relationship_uuid,
        response_status=200,
        response_projection={"relationship_uuid": relationship.relationship_uuid},
    )
    await db_session.commit()
    await db_session.refresh(relationship)
    return _relationship_read(relationship, profile)


async def restrict_relationship_authority(
    db_session: AsyncSession,
    organization: Organization,
    reviewer: PublicUser,
    learner_uuid: str,
    relationship_uuid: str,
    request: StudyRelationshipRestrictionCreate,
    idempotency_key: str,
) -> StudyRelationshipRead:
    """Narrow or end one authority record; every transition disables all grants."""
    await lock_organization(db_session, int(organization.id))
    profile = await _locked_profile(db_session, organization, learner_uuid)
    relationship_result = await db_session.execute(
        select(StudyRelationship)
        .where(
            StudyRelationship.org_id == organization.id,
            StudyRelationship.learner_profile_id == profile.id,
            StudyRelationship.relationship_uuid == relationship_uuid,
        )
        .with_for_update()
    )
    relationship = relationship_result.scalars().first()
    if relationship is None:
        raise HTTPException(status_code=404, detail="Relationship request not found")

    now = datetime.now(timezone.utc)
    await authorize_studytomy_staff_action(
        db_session,
        reviewer,
        organization,
        StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW,
        target_resource_uuid=profile.learner_uuid,
        target_scope_kind="learner",
        subject_user_id=int(profile.user_id),
        excluded_user_ids={int(profile.user_id), int(relationship.adult_user_id)},
        access_version=profile.access_version,
        hide_existence_on_denial=True,
        now=now,
    )
    policies, _ = await _current_restriction_policies(db_session, organization, now)
    current_policy_ids = [int(policy.id) for policy in policies]
    await lock_users(
        db_session,
        [int(reviewer.id), int(profile.user_id), int(relationship.adult_user_id)],
    )

    replay = await claim_idempotency(
        db_session,
        org_id=int(organization.id),
        actor_user_id=int(reviewer.id),
        operation="studytomy.relationship.restrict",
        idempotency_key=idempotency_key,
        request_payload={
            "learner_uuid": learner_uuid,
            "relationship_uuid": relationship_uuid,
            "transition": request.model_dump(mode="json"),
        },
    )
    if replay is not None:
        if replay.resource_uuid != relationship.relationship_uuid:
            raise HTTPException(status_code=409, detail="The idempotent relationship transition is unavailable")
        return _relationship_read(relationship, profile)

    if request.expected_version != relationship.authority_version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STALE_AUTHORITY_VERSION",
                "message": "The relationship changed. Reload it before recording a restriction.",
                "current_version": relationship.authority_version,
            },
        )
    permitted_source_statuses = {
        StudyRelationshipRestrictionAction.RESTRICT: {
            StudyRelationshipStatus.ACCEPTED.value,
        },
        StudyRelationshipRestrictionAction.DISPUTE: {
            StudyRelationshipStatus.ACCEPTED.value,
            StudyRelationshipStatus.RESTRICTED.value,
        },
        StudyRelationshipRestrictionAction.REVOKE: {
            StudyRelationshipStatus.ACCEPTED.value,
            StudyRelationshipStatus.RESTRICTED.value,
            StudyRelationshipStatus.DISPUTED.value,
        },
    }[request.action]
    action_statuses = {
        StudyRelationshipRestrictionAction.RESTRICT: StudyRelationshipStatus.RESTRICTED,
        StudyRelationshipRestrictionAction.DISPUTE: StudyRelationshipStatus.DISPUTED,
        StudyRelationshipRestrictionAction.REVOKE: StudyRelationshipStatus.REVOKED,
    }
    if relationship.status not in permitted_source_statuses:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "RELATIONSHIP_TRANSITION_NOT_ALLOWED",
                "message": "This relationship state cannot receive the requested narrowing transition.",
            },
        )

    owner_assignment = await _reviewer_owner_assignment(
        db_session,
        organization,
        int(reviewer.id),
        profile.learner_uuid,
        now,
    )
    before_state = {
        "status": relationship.status,
        "approved_scopes": relationship.approved_scopes,
        "authority_version": relationship.authority_version,
        "access_version": profile.access_version,
    }
    relationship.status = action_statuses[request.action].value
    relationship.approved_scopes = []
    relationship.decision_policy_version_ids = current_policy_ids
    relationship.decided_by_user_id = int(reviewer.id)
    relationship.decision_owner_assignment_id = int(owner_assignment.id)
    relationship.decision_evidence_reference = request.evidence_reference
    relationship.decision_reason_code = request.reason_code
    relationship.decided_at = now
    relationship.authority_version += 1
    relationship.updated_at = now
    profile.access_version += 1
    profile.updated_at = now
    db_session.add(relationship)
    db_session.add(profile)
    await db_session.flush()
    invalidate_protected_studytomy_user_ids(db_session)
    await append_business_event(
        db_session,
        org_id=int(organization.id),
        actor_user_id=int(reviewer.id),
        actor_capacity=StudyOwnerFunction.FAMILY_SERVICES.value,
        subject_uuid=profile.learner_uuid,
        resource_kind="relationship",
        resource_uuid=relationship.relationship_uuid,
        action={
            StudyRelationshipRestrictionAction.RESTRICT: "studytomy.relationship.restricted",
            StudyRelationshipRestrictionAction.DISPUTE: "studytomy.relationship.disputed",
            StudyRelationshipRestrictionAction.REVOKE: "studytomy.relationship.revoked",
        }[request.action],
        reason_code={
            StudyRelationshipRestrictionAction.RESTRICT: "RELATIONSHIP_RESTRICTED",
            StudyRelationshipRestrictionAction.DISPUTE: "RELATIONSHIP_DISPUTED",
            StudyRelationshipRestrictionAction.REVOKE: "RELATIONSHIP_REVOKED",
        }[request.action],
        before_state=before_state,
        after_state={
            "status": relationship.status,
            "approved_scopes": relationship.approved_scopes,
            "authority_version": relationship.authority_version,
            "access_version": profile.access_version,
        },
        policy_refs=current_policy_ids,
        dedupe_key=f"studytomy:relationship:{relationship.relationship_uuid}:v{relationship.authority_version}",
    )
    await complete_idempotency(
        db_session,
        org_id=int(organization.id),
        actor_user_id=int(reviewer.id),
        operation="studytomy.relationship.restrict",
        idempotency_key=idempotency_key,
        resource_kind="relationship",
        resource_uuid=relationship.relationship_uuid,
        response_status=200,
        response_projection={"relationship_uuid": relationship.relationship_uuid},
    )
    await db_session.commit()
    await db_session.refresh(relationship)
    return _relationship_read(relationship, profile)


async def has_active_guardian_scope(
    db_session: AsyncSession,
    organization: Organization,
    profile: StudyLearnerProfile,
    adult_user_id: int,
    scope_code: str,
    *,
    now: datetime | None = None,
    relationship_binding: tuple[str, int] | None = None,
) -> bool:
    """Check one effective grant and, when supplied, its exact recorded version."""
    if (
        not isinstance(scope_code, str)
        or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", scope_code) is None
        or profile.org_id != organization.id
        or profile.status != StudyLearnerProfileStatus.ACTIVE.value
        or profile.control_mode != StudyLearnerControlMode.DEPENDENT.value
        or int(profile.user_id) == int(adult_user_id)
    ):
        return False
    if not await _require_current_membership(db_session, organization, int(adult_user_id)):
        return False
    effective_now = now or datetime.now(timezone.utc)
    try:
        policies, allowed_scopes = await _current_relationship_policies(
            db_session, organization, effective_now
        )
    except HTTPException:
        return False
    if scope_code not in allowed_scopes:
        return False
    current_policy_ids = [int(policy.id) for policy in policies]
    result = await db_session.execute(
        select(StudyRelationship).where(
            StudyRelationship.org_id == organization.id,
            StudyRelationship.learner_profile_id == profile.id,
            StudyRelationship.adult_user_id == adult_user_id,
            StudyRelationship.relationship_type == StudyRelationshipType.GUARDIAN.value,
            StudyRelationship.status == StudyRelationshipStatus.ACCEPTED.value,
        )
    )
    matching = []
    for relationship in result.scalars().all():
        valid_from = _as_utc(relationship.valid_from) if relationship.valid_from else None
        valid_to = _as_utc(relationship.valid_to) if relationship.valid_to else None
        if (
            valid_from is not None
            and valid_to is not None
            and valid_from <= effective_now < valid_to
            and relationship.decision_policy_version_ids == current_policy_ids
            and set(relationship.approved_scopes).issubset(allowed_scopes)
            and scope_code in relationship.approved_scopes
        ):
            matching.append(relationship)
    if len(matching) != 1:
        return False
    relationship = matching[0]
    return relationship_binding is None or relationship_binding == (
        relationship.relationship_uuid,
        relationship.authority_version,
    )
