"""Individual learner profiles and policy-bound eligibility decisions (TASK-009).

Eligibility is a recorded human decision, not an age/rules calculation. A
profile is created pending and never grants course access. Every decision is
bound to current approved policy versions, a named reviewer assignment and an
opaque evidence reference; later decisions supersede earlier rows.
"""

import re
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import exists, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.family import (
    StudyEligibilityDecision,
    StudyEligibilityDecisionCreate,
    StudyEligibilityDecisionRead,
    StudyEligibilityKind,
    StudyEligibilityOutcome,
    StudyLearnerControlMode,
    StudyLearnerProfile,
    StudyLearnerProfileCreate,
    StudyLearnerProfileRead,
    StudyLearnerProfileStatus,
)
from src.db.studytomy.governance import (
    StudyOwnerAssignment,
    StudyOwnerFunction,
    StudyOwnerStatus,
    StudyPolicyApproval,
    StudyPolicyKey,
    StudyPolicyStatus,
    StudyPolicyVersion,
    StudyStaffScope,
    inspect_study_policy_body,
)
from src.db.studytomy.operations import StudyEvidenceFile
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_studytomy_staff_action,
)
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.idempotency import claim_idempotency, complete_idempotency
from src.services.studytomy.policies import (
    POLICY_REQUIRED_RESPONSIBILITIES,
    _as_utc,
    lock_organization,
    lock_users,
)
from src.services.studytomy.privacy import invalidate_protected_studytomy_user_ids

LEARNER_INTAKE_SCOPE = "learner_intake"


def _policy_keys_for_kind(kind: StudyEligibilityKind) -> tuple[StudyPolicyKey, ...]:
    if kind == StudyEligibilityKind.LEARNING:
        return (StudyPolicyKey.D_01, StudyPolicyKey.D_02, StudyPolicyKey.D_12)
    if kind == StudyEligibilityKind.INDEPENDENT_PARTICIPATION:
        return (StudyPolicyKey.D_01, StudyPolicyKey.D_03, StudyPolicyKey.D_12)
    return (
        StudyPolicyKey.D_01,
        StudyPolicyKey.D_03,
        StudyPolicyKey.D_05,
        StudyPolicyKey.D_12,
    )


def _require_independent_transition_decisions(
    policies: list[StudyPolicyVersion],
) -> None:
    """Do not mark a learner independent while its required policy areas are open."""
    by_key = {policy.policy_key: policy for policy in policies}
    required = (
        (StudyPolicyKey.D_01, "independent_participation"),
        (StudyPolicyKey.D_03, "guardian_authority"),
        (StudyPolicyKey.D_03, "adult_transition"),
        (StudyPolicyKey.D_12, "information_uses"),
        (StudyPolicyKey.D_12, "consent"),
        (StudyPolicyKey.D_12, "parent_report_scope"),
    )
    bodies = {}
    for key in {key for key, _ in required}:
        policy = by_key.get(key.value)
        if policy is None:
            raise HTTPException(
                status_code=409,
                detail={"code": "POLICY_NOT_APPROVED", "message": f"An active {key.value} decision is required."},
            )
        body, blockers = inspect_study_policy_body(key, policy.body)
        if body is None or any(blockers.values()):
            raise HTTPException(
                status_code=409,
                detail={"code": "POLICY_BODY_INVALID", "message": f"The active {key.value} decision is incomplete."},
            )
        bodies[key] = body
    if any(bodies[key].specified_value(area) is None for key, area in required):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POLICY_DECISION_UNRESOLVED",
                "message": "Independent participation requires explicit D-01, D-03 and D-12 decisions.",
            },
        )
    scopes = bodies[StudyPolicyKey.D_12].specified_value("parent_report_scope")
    if (
        not isinstance(scopes, list)
        or not scopes
        or len(scopes) > 64
        or any(
            not isinstance(scope, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", scope) is None
            for scope in scopes
        )
        or len(set(scopes)) != len(scopes)
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POLICY_SCOPE_INVALID",
                "message": "The active D-12 parent-report scope must be a non-empty list of unique scope codes.",
            },
        )


async def _active_policy(
    db_session: AsyncSession,
    organization: Organization,
    policy_key: StudyPolicyKey,
    now: datetime,
) -> StudyPolicyVersion:
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
    if not matches:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POLICY_NOT_APPROVED",
                "message": f"An active approved {policy_key.value} decision is required.",
            },
        )

    policy = matches[0]
    body, body_blockers = inspect_study_policy_body(policy_key, policy.body)
    if body is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POLICY_BODY_INVALID",
                "message": f"The approved {policy_key.value} policy body does not match its decision contract.",
            },
        )
    if any(body_blockers.values()):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POLICY_BODY_INCOMPLETE",
                "message": f"The approved {policy_key.value} policy is missing decision evidence.",
            },
        )

    for responsibility in POLICY_REQUIRED_RESPONSIBILITIES[policy_key]:
        approval_result = await db_session.execute(
            select(StudyPolicyApproval).where(
                StudyPolicyApproval.org_id == organization.id,
                StudyPolicyApproval.policy_version_id == policy.id,
                StudyPolicyApproval.responsibility == responsibility.value,
            )
        )
        approval = approval_result.scalars().first()
        if approval is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "POLICY_APPROVAL_EVIDENCE_MISSING",
                    "message": f"Decision {policy_key.value} is missing required named-owner approval evidence.",
                },
            )
        owner = await db_session.get(StudyOwnerAssignment, approval.owner_assignment_id)
        if (
            owner is None
            or owner.org_id != organization.id
            or owner.responsibility != responsibility.value
            or owner.scope_kind != "organization"
            or owner.scope_uuid != organization.org_uuid
            or owner.status != StudyOwnerStatus.ACTIVE.value
            or owner.user_id != approval.approver_user_id
            or _as_utc(owner.effective_from) > now
            or (owner.effective_to is not None and _as_utc(owner.effective_to) <= now)
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "POLICY_APPROVER_NO_LONGER_ACTIVE",
                    "message": f"Decision {policy_key.value} has an approval without a current named owner.",
                },
            )
        membership = await db_session.execute(
            select(UserOrganization.id).where(
                UserOrganization.org_id == organization.id,
                UserOrganization.user_id == owner.user_id,
            )
        )
        if membership.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "POLICY_APPROVER_NO_LONGER_ACTIVE",
                    "message": f"Decision {policy_key.value} has an approval without a current organization member.",
                },
            )
    return policy


async def _current_policies(
    db_session: AsyncSession,
    organization: Organization,
    keys: tuple[StudyPolicyKey, ...],
    now: datetime,
) -> list[StudyPolicyVersion]:
    return [
        await _active_policy(db_session, organization, key, now)
        for key in keys
    ]


async def _reviewer_assignment(
    db_session: AsyncSession,
    organization: Organization,
    reviewer_user_id: int,
    *,
    scope_kind: str,
    scope_uuid: str,
    capability: StudytomyCapabilityName,
    now: datetime,
) -> StudyOwnerAssignment:
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
            StudyStaffScope.capability == capability.value,
            StudyStaffScope.scope_kind == scope_kind,
            StudyStaffScope.scope_uuid == scope_uuid,
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
    raise HTTPException(status_code=409, detail="The assigned family-services reviewer is no longer active")


async def _latest_decisions(
    db_session: AsyncSession,
    profile: StudyLearnerProfile,
) -> list[StudyEligibilityDecision]:
    successor = StudyEligibilityDecision.__table__.alias("eligibility_successor")
    has_successor = exists(
        select(1).select_from(successor).where(
            successor.c.supersedes_id == StudyEligibilityDecision.id
        )
    )
    result = await db_session.execute(
        select(StudyEligibilityDecision)
        .where(
            StudyEligibilityDecision.org_id == profile.org_id,
            StudyEligibilityDecision.learner_profile_id == profile.id,
            ~has_successor,
        )
        .order_by(StudyEligibilityDecision.created_at.desc(), StudyEligibilityDecision.id.desc())
    )
    rows = list(result.scalars().all())
    by_kind: dict[str, StudyEligibilityDecision] = {}
    for row in rows:
        if row.eligibility_kind in by_kind:
            raise HTTPException(
                status_code=409,
                detail="Multiple current eligibility decisions exist for one learner and decision type",
            )
        by_kind[row.eligibility_kind] = row
    return list(by_kind.values())


async def _decision_read(
    db_session: AsyncSession,
    organization: Organization,
    decision: StudyEligibilityDecision,
    now: datetime,
) -> StudyEligibilityDecisionRead:
    kind = StudyEligibilityKind(decision.eligibility_kind)
    try:
        active = await _current_policies(
            db_session,
            organization,
            _policy_keys_for_kind(kind),
            now,
        )
        policy_current = [int(policy.id) for policy in active] == decision.policy_version_ids
    except HTTPException:
        policy_current = False
    valid_now = _as_utc(decision.effective_from) <= now and (
        decision.effective_to is None or _as_utc(decision.effective_to) > now
    ) and (decision.review_due_at is None or _as_utc(decision.review_due_at) > now)
    return StudyEligibilityDecisionRead(
        decision_uuid=decision.decision_uuid,
        eligibility_kind=kind,
        outcome=StudyEligibilityOutcome(decision.outcome),
        policy_version_ids=decision.policy_version_ids,
        evidence_reference=decision.evidence_reference,
        effective_from=decision.effective_from,
        effective_to=decision.effective_to,
        review_due_at=decision.review_due_at,
        policy_current=policy_current,
        currently_eligible=(
            policy_current
            and valid_now
            and decision.outcome == StudyEligibilityOutcome.ELIGIBLE.value
        ),
        created_at=decision.created_at,
    )


async def _profile_read(
    db_session: AsyncSession,
    organization: Organization,
    profile: StudyLearnerProfile,
) -> StudyLearnerProfileRead:
    user = await db_session.get(User, profile.user_id)
    if user is None:
        raise HTTPException(status_code=409, detail="Learner identity is unavailable")
    decisions = await _latest_decisions(db_session, profile)
    now = datetime.now(timezone.utc)
    return StudyLearnerProfileRead(
        learner_uuid=profile.learner_uuid,
        user_uuid=user.user_uuid,
        status=StudyLearnerProfileStatus(profile.status),
        control_mode=StudyLearnerControlMode(profile.control_mode),
        time_zone=profile.time_zone,
        access_version=profile.access_version,
        eligibility_decisions=[
            await _decision_read(db_session, organization, decision, now)
            for decision in decisions
        ],
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    )


async def create_learner_profile(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    request: StudyLearnerProfileCreate,
    idempotency_key: str,
) -> StudyLearnerProfileRead:
    """Create a pending profile for an existing member under learner-intake scope."""
    await lock_organization(db_session, organization.id)
    user_result = await db_session.execute(
        select(User).where(User.user_uuid == request.user_uuid)
    )
    learner_user = user_result.scalars().first()
    if learner_user is None:
        raise HTTPException(status_code=404, detail="Organization learner not found")
    membership = await db_session.execute(
        select(UserOrganization.id).where(
            UserOrganization.org_id == organization.id,
            UserOrganization.user_id == learner_user.id,
        )
    )
    if membership.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Organization learner not found")

    now = datetime.now(timezone.utc)
    await authorize_studytomy_staff_action(
        db_session,
        actor,
        organization,
        StudytomyCapabilityName.LEARNER_ELIGIBILITY_REVIEW,
        target_resource_uuid=LEARNER_INTAKE_SCOPE,
        target_scope_kind="function",
        now=now,
    )
    policies = await _current_policies(
        db_session,
        organization,
        (
            StudyPolicyKey.D_01,
            StudyPolicyKey.D_02,
            StudyPolicyKey.D_03,
            StudyPolicyKey.D_12,
        ),
        now,
    )
    await lock_users(db_session, [actor.id, learner_user.id])

    replay = await claim_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor.id,
        operation="studytomy.learner.profile.create",
        idempotency_key=idempotency_key,
        request_payload=request.model_dump(mode="json"),
    )
    if replay is not None:
        profile_result = await db_session.execute(
            select(StudyLearnerProfile).where(
                StudyLearnerProfile.org_id == organization.id,
                StudyLearnerProfile.learner_uuid == replay.resource_uuid,
            )
        )
        profile = profile_result.scalars().first()
        if profile is None:
            raise HTTPException(status_code=409, detail="The idempotent learner profile is unavailable")
        return await _profile_read(db_session, organization, profile)

    existing_result = await db_session.execute(
        select(StudyLearnerProfile).where(
            StudyLearnerProfile.org_id == organization.id,
            StudyLearnerProfile.user_id == learner_user.id,
        )
    )
    if existing_result.scalars().first() is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "LEARNER_PROFILE_ALREADY_EXISTS",
                "message": "This organization user already has an individual learner profile.",
            },
        )

    profile = StudyLearnerProfile(
        org_id=organization.id,
        user_id=learner_user.id,
        status=StudyLearnerProfileStatus.PENDING.value,
        control_mode=StudyLearnerControlMode.UNRESOLVED.value,
        time_zone=request.time_zone,
        access_version=1,
        created_by_user_id=actor.id,
        created_at=now,
        updated_at=now,
    )
    db_session.add(profile)
    await db_session.flush()
    invalidate_protected_studytomy_user_ids(db_session)
    await append_business_event(
        db_session,
        org_id=organization.id,
        actor_user_id=actor.id,
        actor_capacity=StudyOwnerFunction.FAMILY_SERVICES.value,
        subject_uuid=profile.learner_uuid,
        resource_kind="learner_profile",
        resource_uuid=profile.learner_uuid,
        action="studytomy.learner.profile_created",
        reason_code="LEARNER_PROFILE_CREATED",
        after_state={
            "status": profile.status,
            "control_mode": profile.control_mode,
            "access_version": profile.access_version,
        },
        policy_refs=[int(policy.id) for policy in policies],
        dedupe_key=f"studytomy:learner-profile:{profile.learner_uuid}:created",
    )
    await complete_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor.id,
        operation="studytomy.learner.profile.create",
        idempotency_key=idempotency_key,
        resource_kind="learner_profile",
        resource_uuid=profile.learner_uuid,
        response_status=201,
        response_projection={"learner_uuid": profile.learner_uuid},
    )
    await db_session.commit()
    await db_session.refresh(profile)
    return await _profile_read(db_session, organization, profile)


async def get_learner_profile(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    learner_uuid: str,
) -> StudyLearnerProfileRead:
    result = await db_session.execute(
        select(StudyLearnerProfile).where(
            StudyLearnerProfile.org_id == organization.id,
            StudyLearnerProfile.learner_uuid == learner_uuid,
        )
    )
    profile = result.scalars().first()
    if profile is None:
        raise HTTPException(status_code=404, detail="Learner profile not found")
    await authorize_studytomy_staff_action(
        db_session,
        actor,
        organization,
        StudytomyCapabilityName.LEARNER_DOSSIER_READ,
        target_resource_uuid=profile.learner_uuid,
        target_scope_kind="learner",
        subject_user_id=profile.user_id,
        access_version=profile.access_version,
        hide_existence_on_denial=True,
    )
    await _current_policies(
        db_session,
        organization,
        (StudyPolicyKey.D_12,),
        datetime.now(timezone.utc),
    )
    return await _profile_read(db_session, organization, profile)


async def record_eligibility_decision(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    learner_uuid: str,
    request: StudyEligibilityDecisionCreate,
    idempotency_key: str,
) -> StudyEligibilityDecisionRead:
    """Append a human eligibility decision without calculating business rules."""
    await lock_organization(db_session, organization.id)
    profile_result = await db_session.execute(
        select(StudyLearnerProfile)
        .where(
            StudyLearnerProfile.org_id == organization.id,
            StudyLearnerProfile.learner_uuid == learner_uuid,
        )
        .with_for_update()
    )
    profile = profile_result.scalars().first()
    if profile is None:
        raise HTTPException(status_code=404, detail="Learner profile not found")
    now = datetime.now(timezone.utc)
    await authorize_studytomy_staff_action(
        db_session,
        actor,
        organization,
        StudytomyCapabilityName.LEARNER_ELIGIBILITY_REVIEW,
        target_resource_uuid=profile.learner_uuid,
        target_scope_kind="learner",
        subject_user_id=profile.user_id,
        access_version=profile.access_version,
        hide_existence_on_denial=True,
        now=now,
    )
    await lock_users(db_session, [actor.id, profile.user_id])

    replay = await claim_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor.id,
        operation="studytomy.learner.eligibility.record",
        idempotency_key=idempotency_key,
        request_payload={
            "learner_uuid": learner_uuid,
            "decision": request.model_dump(mode="json"),
        },
    )
    if replay is not None:
        decision_result = await db_session.execute(
            select(StudyEligibilityDecision).where(
                StudyEligibilityDecision.org_id == organization.id,
                StudyEligibilityDecision.decision_uuid == replay.resource_uuid,
            )
        )
        decision = decision_result.scalars().first()
        if decision is None:
            raise HTTPException(status_code=409, detail="The idempotent eligibility decision is unavailable")
        return await _decision_read(db_session, organization, decision, now)

    if profile.status != StudyLearnerProfileStatus.PENDING.value:
        raise HTTPException(
            status_code=409,
            detail="Only a pending learner profile can receive a new eligibility decision in this release.",
        )
    if request.effective_to is not None and _as_utc(request.effective_to) <= now:
        raise HTTPException(status_code=422, detail="effective_to must be in the future")
    if request.review_due_at is not None and _as_utc(request.review_due_at) <= now:
        raise HTTPException(status_code=422, detail="review_due_at must be in the future")

    policies = await _current_policies(
        db_session,
        organization,
        _policy_keys_for_kind(request.eligibility_kind),
        now,
    )
    if request.eligibility_kind == StudyEligibilityKind.INDEPENDENT_PARTICIPATION:
        _require_independent_transition_decisions(policies)
    if request.evidence_reference.startswith("ev_"):
        evidence_result = await db_session.execute(
            select(StudyEvidenceFile).where(
                StudyEvidenceFile.org_id == organization.id,
                StudyEvidenceFile.learner_profile_id == profile.id,
                StudyEvidenceFile.evidence_uuid == request.evidence_reference,
                StudyEvidenceFile.purpose == "eligibility_evidence",
                StudyEvidenceFile.upload_status == "ready",
            )
        )
        evidence = evidence_result.scalars().first()
        if evidence is None:
            raise HTTPException(
                status_code=422,
                detail="The evidence reference is not a ready attachment for this learner.",
            )
        evidence_policies = await _current_policies(
            db_session,
            organization,
            (
                StudyPolicyKey.D_01,
                StudyPolicyKey.D_03,
                StudyPolicyKey.D_12,
                StudyPolicyKey.D_13,
                StudyPolicyKey.D_15,
            ),
            now,
        )
        if evidence.policy_version_ids != [int(policy.id) for policy in evidence_policies]:
            raise HTTPException(
                status_code=409,
                detail="The evidence attachment is governed by superseded policy versions.",
            )
    owner = await _reviewer_assignment(
        db_session,
        organization,
        actor.id,
        scope_kind="learner",
        scope_uuid=profile.learner_uuid,
        capability=StudytomyCapabilityName.LEARNER_ELIGIBILITY_REVIEW,
        now=now,
    )
    prior_decisions = [
        decision
        for decision in await _latest_decisions(db_session, profile)
        if decision.eligibility_kind == request.eligibility_kind.value
    ]
    prior = prior_decisions[0] if prior_decisions else None
    previous_control_mode = profile.control_mode
    if request.eligibility_kind == StudyEligibilityKind.INDEPENDENT_PARTICIPATION:
        if request.outcome == StudyEligibilityOutcome.ELIGIBLE:
            profile.control_mode = StudyLearnerControlMode.INDEPENDENT.value
        elif profile.control_mode == StudyLearnerControlMode.INDEPENDENT.value:
            profile.control_mode = StudyLearnerControlMode.UNRESOLVED.value
    profile.access_version += 1
    profile.updated_at = now

    decision = StudyEligibilityDecision(
        org_id=organization.id,
        learner_profile_id=profile.id,
        eligibility_kind=request.eligibility_kind.value,
        outcome=request.outcome.value,
        policy_version_ids=[int(policy.id) for policy in policies],
        evidence_reference=request.evidence_reference,
        effective_from=now,
        effective_to=request.effective_to,
        review_due_at=request.review_due_at,
        reviewer_user_id=actor.id,
        owner_assignment_id=owner.id,
        supersedes_id=prior.id if prior is not None else None,
        created_at=now,
    )
    db_session.add(decision)
    await db_session.flush()
    db_session.add(profile)
    await append_business_event(
        db_session,
        org_id=organization.id,
        actor_user_id=actor.id,
        actor_capacity=StudyOwnerFunction.FAMILY_SERVICES.value,
        subject_uuid=profile.learner_uuid,
        resource_kind="eligibility_decision",
        resource_uuid=decision.decision_uuid,
        action="studytomy.learner.eligibility_decided",
        reason_code="LEARNER_ELIGIBILITY_REVIEWED",
        before_state={
            "control_mode": previous_control_mode,
            "access_version": profile.access_version - 1,
        },
        after_state={
            "decision_code": decision.outcome,
            "eligibility_kind": decision.eligibility_kind,
            "control_mode": profile.control_mode,
            "access_version": profile.access_version,
        },
        policy_refs=decision.policy_version_ids,
        dedupe_key=f"studytomy:eligibility:{decision.decision_uuid}:created",
    )
    await complete_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor.id,
        operation="studytomy.learner.eligibility.record",
        idempotency_key=idempotency_key,
        resource_kind="eligibility_decision",
        resource_uuid=decision.decision_uuid,
        response_status=201,
        response_projection={"decision_uuid": decision.decision_uuid},
    )
    await db_session.commit()
    await db_session.refresh(decision)
    return await _decision_read(db_session, organization, decision, now)
