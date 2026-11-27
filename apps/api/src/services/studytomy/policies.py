"""Use cases for explicit Studytomy policy and service-owner decisions."""

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.governance import (
    StudyFinancialDelegationCreate,
    StudyFinancialDelegationRead,
    StudyOwnerAssignment,
    StudyOwnerAssignmentCreate,
    StudyOwnerAssignmentRead,
    StudyOwnerFunction,
    StudyOwnerStatus,
    StudyPolicyApproval,
    StudyPolicyApprovalRead,
    StudyPolicyApprovalRequest,
    StudyPolicyKey,
    StudyPolicyProposal,
    StudyPolicyStatus,
    StudyPolicySupersedeRequest,
    StudyPolicyVersion,
    StudyPolicyVersionRead,
    StudyStaffScope,
    StudyStaffScopeCreate,
    StudyStaffScopeRead,
    StudyStaffScopeRevoke,
    inspect_study_policy_body,
)
from src.db.user_organizations import UserOrganization
from src.db.users import User
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.idempotency import claim_idempotency, complete_idempotency


async def _owner_assignment_read(
    db_session: AsyncSession, assignment: StudyOwnerAssignment
) -> StudyOwnerAssignmentRead:
    owner = await db_session.get(User, assignment.user_id)
    assigned_by = await db_session.get(User, assignment.assigned_by_user_id)
    ended_by = (
        await db_session.get(User, assignment.ended_by_user_id)
        if assignment.ended_by_user_id is not None
        else None
    )
    if owner is None or assigned_by is None:
        raise HTTPException(status_code=409, detail="Service-owner identity is unavailable")
    return StudyOwnerAssignmentRead(
        owner_uuid=assignment.owner_uuid,
        user_uuid=owner.user_uuid,
        responsibility=StudyOwnerFunction(assignment.responsibility),
        scope_kind=assignment.scope_kind,
        scope_uuid=assignment.scope_uuid,
        status=StudyOwnerStatus(assignment.status),
        evidence_reference=assignment.evidence_reference,
        effective_from=assignment.effective_from,
        effective_to=assignment.effective_to,
        assigned_by_user_uuid=assigned_by.user_uuid,
        ended_by_user_uuid=ended_by.user_uuid if ended_by else None,
        ended_at=assignment.ended_at,
        end_evidence_reference=assignment.end_evidence_reference,
        created_at=assignment.created_at,
    )


async def _staff_scope_read(
    db_session: AsyncSession, scope: StudyStaffScope
) -> StudyStaffScopeRead:
    owner = await db_session.get(StudyOwnerAssignment, scope.assignment_id)
    creator = await db_session.get(User, scope.created_by_user_id)
    if owner is None or creator is None:
        raise HTTPException(status_code=409, detail="Staff scope identity is unavailable")
    from src.db.studytomy.governance import StudyStaffScopeKind

    return StudyStaffScopeRead(
        scope_record_uuid=scope.scope_record_uuid,
        owner_uuid=owner.owner_uuid,
        capability=scope.capability,
        scope_kind=StudyStaffScopeKind(scope.scope_kind),
        scope_uuid=scope.scope_uuid,
        is_exclusion=scope.is_exclusion,
        status=scope.status,
        evidence_reference=scope.evidence_reference,
        created_by_user_uuid=creator.user_uuid,
        created_at=scope.created_at,
    )


POLICY_REQUIRED_RESPONSIBILITIES: dict[StudyPolicyKey, frozenset[StudyOwnerFunction]] = {
    StudyPolicyKey.D_01: frozenset({StudyOwnerFunction.BUSINESS, StudyOwnerFunction.SAFEGUARDING}),
    StudyPolicyKey.D_02: frozenset({StudyOwnerFunction.EDUCATION}),
    StudyPolicyKey.D_03: frozenset({StudyOwnerFunction.FAMILY_SERVICES}),
    StudyPolicyKey.D_04: frozenset({StudyOwnerFunction.SUPPORT, StudyOwnerFunction.EDUCATION}),
    StudyPolicyKey.D_05: frozenset({StudyOwnerFunction.COMMERCIAL}),
    StudyPolicyKey.D_06: frozenset({StudyOwnerFunction.COMMERCIAL}),
    StudyPolicyKey.D_07: frozenset({StudyOwnerFunction.COMMERCIAL, StudyOwnerFunction.SUPPORT}),
    StudyPolicyKey.D_08: frozenset({StudyOwnerFunction.EDUCATION}),
    StudyPolicyKey.D_09: frozenset({StudyOwnerFunction.EDUCATION}),
    StudyPolicyKey.D_10: frozenset({StudyOwnerFunction.EDUCATION, StudyOwnerFunction.SAFEGUARDING}),
    StudyPolicyKey.D_11: frozenset({StudyOwnerFunction.SAFEGUARDING}),
    StudyPolicyKey.D_12: frozenset({StudyOwnerFunction.BUSINESS, StudyOwnerFunction.FAMILY_SERVICES}),
    StudyPolicyKey.D_13: frozenset({StudyOwnerFunction.BUSINESS}),
    StudyPolicyKey.D_14: frozenset({StudyOwnerFunction.FAMILY_SERVICES}),
    StudyPolicyKey.D_15: frozenset({StudyOwnerFunction.EDUCATION, StudyOwnerFunction.SERVICE}),
    StudyPolicyKey.D_16: frozenset({StudyOwnerFunction.EDUCATION}),
    StudyPolicyKey.D_17: frozenset({StudyOwnerFunction.BUSINESS, StudyOwnerFunction.EDUCATION}),
    StudyPolicyKey.D_18: frozenset({StudyOwnerFunction.BUSINESS}),
    StudyPolicyKey.D_19: frozenset({StudyOwnerFunction.BUSINESS, StudyOwnerFunction.COMMERCIAL}),
    StudyPolicyKey.D_20: frozenset({StudyOwnerFunction.COMMERCIAL}),
}


def _raise_conflict(message: str) -> None:
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message)


def _as_utc(value: datetime) -> datetime:
    """Normalise database timestamps, including SQLite's timezone-stripped values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _validate_effective_range(start: datetime, end: datetime | None) -> None:
    if start.tzinfo is None or (end is not None and end.tzinfo is None):
        raise HTTPException(status_code=422, detail="Effective timestamps must include a timezone")
    if end is not None and end <= start:
        raise HTTPException(status_code=422, detail="effective_to must be later than effective_from")


async def lock_organization(db_session: AsyncSession, org_id: int) -> Organization:
    """Serialize governance mutations per organization on PostgreSQL."""
    result = await db_session.execute(
        select(Organization).where(Organization.id == org_id).with_for_update()
    )
    organization = result.scalars().first()
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return organization


async def lock_users(db_session: AsyncSession, user_ids: list[int]) -> dict[int, User]:
    """Lock referenced identities in stable order before writing governance rows."""
    users: dict[int, User] = {}
    for user_id in sorted(set(user_ids)):
        result = await db_session.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        user = result.scalars().first()
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        users[user_id] = user
    return users


async def require_org_member_row(
    db_session: AsyncSession, org_id: int, user_id: int
) -> None:
    """Recheck membership after taking the organization governance lock."""
    result = await db_session.execute(
        select(UserOrganization.id).where(
            UserOrganization.org_id == org_id,
            UserOrganization.user_id == user_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=403, detail="Organization membership is required")


async def require_org_admin_row(
    db_session: AsyncSession, org_id: int, user_id: int
) -> None:
    """Recheck org administration for governance writes after locking the org."""
    from src.security.rbac.constants import ADMIN_OR_MAINTAINER_ROLE_IDS

    result = await db_session.execute(
        select(UserOrganization.id).where(
            UserOrganization.org_id == org_id,
            UserOrganization.user_id == user_id,
            UserOrganization.role_id.in_(ADMIN_OR_MAINTAINER_ROLE_IDS),
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=403,
            detail="Organization administration is required for this action",
        )


async def create_owner_assignment(
    db_session: AsyncSession,
    organization: Organization,
    actor_user_id: int,
    request: StudyOwnerAssignmentCreate,
    idempotency_key: str | None = None,
) -> StudyOwnerAssignmentRead:
    _validate_effective_range(request.effective_from, request.effective_to)
    user_result = await db_session.execute(
        select(User).where(User.user_uuid == request.user_uuid)
    )
    owner_candidate = user_result.scalars().first()
    if owner_candidate is None:
        raise HTTPException(status_code=404, detail="Organization member not found")
    locked_users = await lock_users(db_session, [actor_user_id, owner_candidate.id])
    owner = locked_users[owner_candidate.id]

    await lock_organization(db_session, organization.id)
    await require_org_admin_row(db_session, organization.id, actor_user_id)
    replay = await claim_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        operation="studytomy.service_owner.assign",
        idempotency_key=idempotency_key,
        request_payload=request,
    )
    if replay is not None:
        existing = await db_session.execute(
            select(StudyOwnerAssignment).where(
                StudyOwnerAssignment.org_id == organization.id,
                StudyOwnerAssignment.owner_uuid == replay.resource_uuid,
            )
        )
        assignment = existing.scalars().first()
        if assignment is None:
            raise HTTPException(status_code=409, detail="The idempotent owner assignment is unavailable")
        return await _owner_assignment_read(db_session, assignment)

    membership_result = await db_session.execute(
        select(UserOrganization).where(
            UserOrganization.org_id == organization.id,
            UserOrganization.user_id == owner.id,
        )
    )
    if membership_result.scalars().first() is None:
        # Do not let a platform superadmin's bypass turn into a staff assignment.
        raise HTTPException(status_code=404, detail="Organization member not found")

    existing_assignments = await db_session.execute(
        select(StudyOwnerAssignment).where(
            StudyOwnerAssignment.org_id == organization.id,
            StudyOwnerAssignment.user_id == owner.id,
            StudyOwnerAssignment.responsibility == request.responsibility.value,
            StudyOwnerAssignment.scope_kind == "organization",
            StudyOwnerAssignment.scope_uuid == organization.org_uuid,
            StudyOwnerAssignment.status == StudyOwnerStatus.ACTIVE.value,
        )
    )
    for existing in existing_assignments.scalars().all():
        if _ranges_overlap(
            request.effective_from,
            request.effective_to,
            existing.effective_from,
            existing.effective_to,
        ):
            _raise_conflict("An active service-owner assignment already overlaps this period")

    assignment = StudyOwnerAssignment(
        org_id=organization.id,
        user_id=owner.id,
        responsibility=request.responsibility.value,
        scope_kind="organization",
        scope_uuid=organization.org_uuid,
        status=StudyOwnerStatus.ACTIVE.value,
        evidence_reference=request.evidence_reference.strip(),
        effective_from=request.effective_from,
        effective_to=request.effective_to,
        assigned_by_user_id=actor_user_id,
    )
    db_session.add(assignment)
    await db_session.flush()
    await append_business_event(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        actor_capacity="organization_admin",
        resource_kind="service_owner",
        resource_uuid=assignment.owner_uuid,
        action="studytomy.service_owner.assigned",
        reason_code="SERVICE_OWNER_ASSIGNED",
        after_state={
            "status": assignment.status,
            "responsibility": assignment.responsibility,
            "scope_kind": assignment.scope_kind,
            "effective_from": assignment.effective_from.isoformat(),
            "effective_to": assignment.effective_to.isoformat() if assignment.effective_to else None,
        },
    )
    await complete_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        operation="studytomy.service_owner.assign",
        idempotency_key=idempotency_key,
        resource_kind="service_owner",
        resource_uuid=assignment.owner_uuid,
        response_status=201,
        response_projection={"owner_uuid": assignment.owner_uuid},
    )
    await db_session.commit()
    await db_session.refresh(assignment)
    return await _owner_assignment_read(db_session, assignment)


async def list_owner_assignments(
    db_session: AsyncSession, organization: Organization, limit: int = 25
) -> list[StudyOwnerAssignmentRead]:
    result = await db_session.execute(
        select(StudyOwnerAssignment)
        .where(StudyOwnerAssignment.org_id == organization.id)
        .order_by(StudyOwnerAssignment.created_at.desc(), StudyOwnerAssignment.id.desc())
        .limit(limit)
    )
    assignments = list(result.scalars().all())
    reads: list[StudyOwnerAssignmentRead] = []
    for assignment in assignments:
        owner = await db_session.get(User, assignment.user_id)
        assigned_by = await db_session.get(User, assignment.assigned_by_user_id)
        ended_by = (
            await db_session.get(User, assignment.ended_by_user_id)
            if assignment.ended_by_user_id is not None
            else None
        )
        if owner is None or assigned_by is None:
            # RESTRICT foreign keys make this a data-integrity problem, not an
            # invitation to expose a partial or misleading owner record.
            raise HTTPException(status_code=409, detail="Service-owner identity is unavailable")
        reads.append(
            StudyOwnerAssignmentRead(
                owner_uuid=assignment.owner_uuid,
                user_uuid=owner.user_uuid,
                responsibility=StudyOwnerFunction(assignment.responsibility),
                scope_kind=assignment.scope_kind,
                scope_uuid=assignment.scope_uuid,
                status=StudyOwnerStatus(assignment.status),
                evidence_reference=assignment.evidence_reference,
                effective_from=assignment.effective_from,
                effective_to=assignment.effective_to,
                assigned_by_user_uuid=assigned_by.user_uuid,
                ended_by_user_uuid=ended_by.user_uuid if ended_by else None,
                ended_at=assignment.ended_at,
                end_evidence_reference=assignment.end_evidence_reference,
                created_at=assignment.created_at,
            )
        )
    return reads


async def end_owner_assignments_for_removed_members(
    db_session: AsyncSession,
    org_id: int,
    removed_user_ids: list[int],
    actor_user_id: int,
) -> None:
    """End owner authority in the same transaction as org membership removal."""
    if not removed_user_ids:
        return
    await lock_organization(db_session, org_id)
    now = datetime.now(timezone.utc)
    result = await db_session.execute(
        select(StudyOwnerAssignment).where(
            StudyOwnerAssignment.org_id == org_id,
            StudyOwnerAssignment.user_id.in_(removed_user_ids),
            StudyOwnerAssignment.status == StudyOwnerStatus.ACTIVE.value,
        )
    )
    for assignment in result.scalars().all():
        before_state = {
            "status": assignment.status,
            "responsibility": assignment.responsibility,
            "scope_kind": assignment.scope_kind,
            "effective_to": assignment.effective_to.isoformat() if assignment.effective_to else None,
        }
        assignment.status = StudyOwnerStatus.ENDED.value
        assignment.ended_by_user_id = actor_user_id
        assignment.ended_at = now
        assignment.end_evidence_reference = "Organization membership ended"
        # A future appointment is disabled by status; only close a currently
        # effective interval to avoid violating its start/end constraint.
        if _as_utc(assignment.effective_from) < now and (
            assignment.effective_to is None or _as_utc(assignment.effective_to) > now
        ):
            assignment.effective_to = now
        owner = await db_session.get(User, assignment.user_id)
        if owner is None:
            raise HTTPException(status_code=409, detail="Service-owner identity is unavailable")
        await append_business_event(
            db_session,
            org_id=org_id,
            actor_user_id=actor_user_id,
            resource_kind="service_owner",
            resource_uuid=assignment.owner_uuid,
            action="studytomy.service_owner.ended",
            reason_code="ORG_MEMBERSHIP_ENDED",
            before_state=before_state,
            after_state={
                "status": assignment.status,
                "responsibility": assignment.responsibility,
                "scope_kind": assignment.scope_kind,
                "effective_to": assignment.effective_to.isoformat() if assignment.effective_to else None,
            },
        )


async def create_staff_scope(
    db_session: AsyncSession,
    organization: Organization,
    actor_user_id: int,
    owner_uuid: str,
    request: StudyStaffScopeCreate,
    idempotency_key: str | None = None,
) -> StudyStaffScopeRead:
    """Grant one evidenced capability over one resource to an active owner assignment."""
    await lock_organization(db_session, organization.id)
    await lock_users(db_session, [actor_user_id])
    await require_org_admin_row(db_session, organization.id, actor_user_id)
    result = await db_session.execute(
        select(StudyOwnerAssignment)
        .where(
            StudyOwnerAssignment.owner_uuid == owner_uuid,
            StudyOwnerAssignment.org_id == organization.id,
            StudyOwnerAssignment.scope_uuid == organization.org_uuid,
        )
        .with_for_update()
    )
    owner_assignment = result.scalars().first()
    if owner_assignment is None:
        raise HTTPException(status_code=404, detail="Service-owner assignment not found")

    replay = await claim_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        operation="studytomy.staff_scope.grant",
        idempotency_key=idempotency_key,
        request_payload={"owner_uuid": owner_uuid, "scope": request.model_dump(mode="json")},
    )
    if replay is not None:
        scope_result = await db_session.execute(
            select(StudyStaffScope).where(
                StudyStaffScope.org_id == organization.id,
                StudyStaffScope.scope_record_uuid == replay.resource_uuid,
            )
        )
        scope = scope_result.scalars().first()
        if scope is None:
            raise HTTPException(status_code=409, detail="The idempotent staff scope is unavailable")
        return await _staff_scope_read(db_session, scope)

    if owner_assignment.status != StudyOwnerStatus.ACTIVE.value:
        _raise_conflict("A scope cannot be added to an ended service-owner assignment")
    now = datetime.now(timezone.utc)
    if _as_utc(owner_assignment.effective_from) > now or (
        owner_assignment.effective_to is not None
        and _as_utc(owner_assignment.effective_to) <= now
    ):
        _raise_conflict("A scope cannot be added to an inactive service-owner assignment")

    owner_membership = await db_session.execute(
        select(UserOrganization.id).where(
            UserOrganization.org_id == organization.id,
            UserOrganization.user_id == owner_assignment.user_id,
        )
    )
    if owner_membership.scalar_one_or_none() is None:
        raise HTTPException(status_code=409, detail="Service owner is no longer an organization member")

    existing_scope = await db_session.execute(
        select(StudyStaffScope.id).where(
            StudyStaffScope.org_id == organization.id,
            StudyStaffScope.assignment_id == owner_assignment.id,
            StudyStaffScope.capability == request.capability,
            StudyStaffScope.scope_kind == request.scope_kind.value,
            StudyStaffScope.scope_uuid == request.scope_uuid,
            StudyStaffScope.is_exclusion == request.is_exclusion,
            StudyStaffScope.status == "active",
        ).limit(1)
    )
    if existing_scope.scalar_one_or_none() is not None:
        _raise_conflict("An active capability scope with the same target already exists")

    scope = StudyStaffScope(
        org_id=organization.id,
        assignment_id=owner_assignment.id,
        capability=request.capability,
        scope_kind=request.scope_kind.value,
        scope_uuid=request.scope_uuid,
        is_exclusion=request.is_exclusion,
        status="active",
        evidence_reference=request.evidence_reference,
        created_by_user_id=actor_user_id,
    )
    db_session.add(scope)
    await db_session.flush()
    owner = await db_session.get(User, owner_assignment.user_id)
    if owner is None:
        raise HTTPException(status_code=409, detail="Service-owner identity is unavailable")
    await append_business_event(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        resource_kind="staff_scope",
        resource_uuid=scope.scope_record_uuid,
        action="studytomy.staff_scope.granted",
        reason_code="STAFF_SCOPE_GRANTED",
        after_state={
            "capability": scope.capability,
            "scope_kind": scope.scope_kind,
            "is_exclusion": scope.is_exclusion,
            "status": scope.status,
        },
    )
    await complete_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        operation="studytomy.staff_scope.grant",
        idempotency_key=idempotency_key,
        resource_kind="staff_scope",
        resource_uuid=scope.scope_record_uuid,
        response_status=201,
        response_projection={"scope_record_uuid": scope.scope_record_uuid},
    )
    await db_session.commit()
    await db_session.refresh(scope)
    return await _staff_scope_read(db_session, scope)


async def set_financial_delegation(
    db_session: AsyncSession,
    organization: Organization,
    actor_user_id: int,
    owner_uuid: str,
    request: StudyFinancialDelegationCreate,
    idempotency_key: str | None = None,
) -> StudyFinancialDelegationRead:
    """Persist one evidenced staff ceiling only under an active approved D-19."""
    await lock_organization(db_session, organization.id)
    await lock_users(db_session, [actor_user_id])
    await require_org_admin_row(db_session, organization.id, actor_user_id)
    now = datetime.now(timezone.utc)
    owner_result = await db_session.execute(
        select(StudyOwnerAssignment)
        .where(
            StudyOwnerAssignment.owner_uuid == owner_uuid,
            StudyOwnerAssignment.org_id == organization.id,
            StudyOwnerAssignment.scope_kind == "organization",
            StudyOwnerAssignment.scope_uuid == organization.org_uuid,
            StudyOwnerAssignment.responsibility == StudyOwnerFunction.COMMERCIAL.value,
        )
        .with_for_update()
    )
    owner_assignment = owner_result.scalars().first()
    if owner_assignment is None:
        raise HTTPException(status_code=404, detail="Commercial service-owner assignment not found")

    replay = await claim_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        operation="studytomy.financial_delegation.set",
        idempotency_key=idempotency_key,
        request_payload={"owner_uuid": owner_uuid, "delegation": request.model_dump(mode="json")},
    )
    if replay is not None:
        projection = replay.response_projection or {}
        try:
            return StudyFinancialDelegationRead.model_validate(projection)
        except Exception as error:
            raise HTTPException(status_code=409, detail="The idempotent delegation response is unavailable") from error

    if owner_assignment.status != StudyOwnerStatus.ACTIVE.value or (
        _as_utc(owner_assignment.effective_from) > now
        or (owner_assignment.effective_to is not None and _as_utc(owner_assignment.effective_to) <= now)
    ):
        _raise_conflict("Financial delegation requires an active commercial owner assignment")

    policy_result = await db_session.execute(
        select(StudyPolicyVersion)
        .where(
            StudyPolicyVersion.policy_uuid == request.policy_uuid,
            StudyPolicyVersion.org_id == organization.id,
            StudyPolicyVersion.policy_key == StudyPolicyKey.D_19.value,
            StudyPolicyVersion.scope_kind == "organization",
            StudyPolicyVersion.scope_uuid == organization.org_uuid,
            StudyPolicyVersion.status == StudyPolicyStatus.APPROVED.value,
            StudyPolicyVersion.effective_from <= now,
        )
        .with_for_update()
    )
    active_policy = next(
        (
            policy
            for policy in policy_result.scalars().all()
            if policy.effective_to is None or _as_utc(policy.effective_to) > now
        ),
        None,
    )
    if active_policy is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POLICY_NOT_APPROVED",
                "message": "An active approved D-19 policy is required for financial delegation.",
            },
        )

    d19_body, d19_blockers = inspect_study_policy_body(
        StudyPolicyKey.D_19, active_policy.body
    )
    if d19_body is None or any(d19_blockers.values()):
        _raise_conflict("A complete D-19 decision body is required before delegation")
    required_delegation_areas = (
        "staff_authority",
        "refund_limits",
        "exceptions",
        "financial_review_frequency",
    )
    if any(d19_body.specified_value(area) is None for area in required_delegation_areas):
        _raise_conflict(
            "D-19 must specify staff authority, refund limits, exceptions and financial review frequency before delegation"
        )

    before_state = {
        "financial_limit_minor": owner_assignment.financial_limit_minor,
        "financial_limit_currency": owner_assignment.financial_limit_currency,
        "policy_version_id": owner_assignment.delegation_policy_version_id,
    }
    owner_assignment.financial_limit_minor = request.max_amount_minor
    owner_assignment.financial_limit_currency = request.currency
    owner_assignment.delegation_evidence_reference = request.evidence_reference
    owner_assignment.delegation_policy_version_id = active_policy.id
    owner_user = await db_session.get(User, owner_assignment.user_id)
    if owner_user is None:
        raise HTTPException(status_code=409, detail="Commercial owner identity is unavailable")
    await append_business_event(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        actor_capacity="organization_admin",
        resource_kind="service_owner",
        resource_uuid=owner_assignment.owner_uuid,
        action="studytomy.financial_delegation.set",
        reason_code="FINANCIAL_DELEGATION_UPDATED",
        before_state=before_state,
        after_state={
            "financial_limit_minor": owner_assignment.financial_limit_minor,
            "financial_limit_currency": owner_assignment.financial_limit_currency,
            "policy_version_id": active_policy.id,
        },
        policy_refs=[active_policy.id],
    )
    await complete_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        operation="studytomy.financial_delegation.set",
        idempotency_key=idempotency_key,
        resource_kind="service_owner",
        resource_uuid=owner_assignment.owner_uuid,
        response_status=200,
        response_projection={
            "owner_uuid": owner_assignment.owner_uuid,
            "currency": request.currency,
            "max_amount_minor": request.max_amount_minor,
            "policy_version_id": active_policy.id,
            "evidence_reference": request.evidence_reference,
        },
    )
    await db_session.commit()
    return StudyFinancialDelegationRead(
        owner_uuid=owner_assignment.owner_uuid,
        currency=owner_assignment.financial_limit_currency,
        max_amount_minor=owner_assignment.financial_limit_minor,
        policy_version_id=active_policy.id,
        evidence_reference=owner_assignment.delegation_evidence_reference,
    )


async def list_staff_scopes(
    db_session: AsyncSession,
    organization: Organization,
    owner_uuid: str,
    limit: int = 50,
) -> list[StudyStaffScopeRead]:
    owner_result = await db_session.execute(
        select(StudyOwnerAssignment).where(
            StudyOwnerAssignment.org_id == organization.id,
            StudyOwnerAssignment.owner_uuid == owner_uuid,
        )
    )
    owner_assignment = owner_result.scalars().first()
    if owner_assignment is None:
        raise HTTPException(status_code=404, detail="Service-owner assignment not found")
    result = await db_session.execute(
        select(StudyStaffScope)
        .where(
            StudyStaffScope.org_id == organization.id,
            StudyStaffScope.assignment_id == owner_assignment.id,
        )
        .order_by(StudyStaffScope.created_at.desc(), StudyStaffScope.id.desc())
        .limit(limit)
    )
    scopes = list(result.scalars().all())
    reads: list[StudyStaffScopeRead] = []
    for scope in scopes:
        creator = await db_session.get(User, scope.created_by_user_id)
        if creator is None:
            raise HTTPException(status_code=409, detail="Scope creator identity is unavailable")
        reads.append(
            StudyStaffScopeRead(
                scope_record_uuid=scope.scope_record_uuid,
                owner_uuid=owner_assignment.owner_uuid,
                capability=scope.capability,
                scope_kind=scope.scope_kind,
                scope_uuid=scope.scope_uuid,
                is_exclusion=scope.is_exclusion,
                status=scope.status,
                evidence_reference=scope.evidence_reference,
                created_by_user_uuid=creator.user_uuid,
                created_at=scope.created_at,
            )
        )
    return reads


async def revoke_staff_scope(
    db_session: AsyncSession,
    organization: Organization,
    actor_user_id: int,
    scope_record_uuid: str,
    request: StudyStaffScopeRevoke,
    idempotency_key: str | None = None,
) -> None:
    await lock_organization(db_session, organization.id)
    await lock_users(db_session, [actor_user_id])
    await require_org_admin_row(db_session, organization.id, actor_user_id)
    result = await db_session.execute(
        select(StudyStaffScope)
        .where(
            StudyStaffScope.scope_record_uuid == scope_record_uuid,
            StudyStaffScope.org_id == organization.id,
        )
        .with_for_update()
    )
    scope = result.scalars().first()
    if scope is None:
        raise HTTPException(status_code=404, detail="Staff scope not found")
    replay = await claim_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        operation="studytomy.staff_scope.revoke",
        idempotency_key=idempotency_key,
        request_payload={
            "scope_record_uuid": scope_record_uuid,
            "revocation": request.model_dump(mode="json"),
        },
    )
    if replay is not None:
        return
    if scope.status != "active":
        _raise_conflict("Staff scope has already been revoked")
    before_state = {
        "capability": scope.capability,
        "scope_kind": scope.scope_kind,
        "is_exclusion": scope.is_exclusion,
        "status": scope.status,
    }
    scope.status = "revoked"
    scope.revoked_by_user_id = actor_user_id
    scope.revoked_at = datetime.now(timezone.utc)
    scope.revocation_evidence_reference = request.evidence_reference
    await append_business_event(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        resource_kind="staff_scope",
        resource_uuid=scope.scope_record_uuid,
        action="studytomy.staff_scope.revoked",
        reason_code="STAFF_SCOPE_REVOKED",
        before_state=before_state,
        after_state={
            "capability": scope.capability,
            "scope_kind": scope.scope_kind,
            "is_exclusion": scope.is_exclusion,
            "status": scope.status,
        },
    )
    await complete_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        operation="studytomy.staff_scope.revoke",
        idempotency_key=idempotency_key,
        resource_kind="staff_scope",
        resource_uuid=scope.scope_record_uuid,
        response_status=204,
    )
    await db_session.commit()


async def propose_policy_version(
    db_session: AsyncSession,
    organization: Organization,
    actor_user_id: int,
    request: StudyPolicyProposal,
    idempotency_key: str | None = None,
) -> StudyPolicyVersionRead:
    _validate_effective_range(request.effective_from, request.effective_to)
    await lock_users(db_session, [actor_user_id])
    await lock_organization(db_session, organization.id)
    await require_org_admin_row(db_session, organization.id, actor_user_id)

    replay = await claim_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        operation="studytomy.policy.propose",
        idempotency_key=idempotency_key,
        request_payload=request,
    )
    if replay is not None:
        policy_result = await db_session.execute(
            select(StudyPolicyVersion).where(
                StudyPolicyVersion.org_id == organization.id,
                StudyPolicyVersion.policy_uuid == replay.resource_uuid,
            )
        )
        policy = policy_result.scalars().first()
        if policy is None:
            raise HTTPException(status_code=409, detail="The idempotent policy version is unavailable")
        return await get_policy_version_read(db_session, organization, policy)

    latest_result = await db_session.execute(
        select(StudyPolicyVersion)
        .where(
            StudyPolicyVersion.org_id == organization.id,
            StudyPolicyVersion.policy_key == request.policy_key.value,
            StudyPolicyVersion.scope_kind == "organization",
            StudyPolicyVersion.scope_uuid == organization.org_uuid,
        )
        .order_by(StudyPolicyVersion.version.desc())
        .limit(1)
    )
    latest = latest_result.scalars().first()
    version = (latest.version + 1) if latest else 1

    supersedes_id = None
    if request.supersedes_policy_uuid:
        previous_result = await db_session.execute(
            select(StudyPolicyVersion).where(
                StudyPolicyVersion.policy_uuid == request.supersedes_policy_uuid,
                StudyPolicyVersion.org_id == organization.id,
            )
        )
        previous = previous_result.scalars().first()
        if (
            previous is None
            or previous.policy_key != request.policy_key.value
            or previous.scope_uuid != organization.org_uuid
            or previous.status != StudyPolicyStatus.APPROVED.value
        ):
            raise HTTPException(status_code=404, detail="Approved policy version not found")
        supersedes_id = previous.id

    policy = StudyPolicyVersion(
        org_id=organization.id,
        policy_key=request.policy_key.value,
        scope_kind="organization",
        scope_uuid=organization.org_uuid,
        version=version,
        status=StudyPolicyStatus.DRAFT.value,
        body=request.body.model_dump(mode="json"),
        evidence_reference=request.evidence_reference.strip(),
        evidence_sha256=request.evidence_sha256.lower() if request.evidence_sha256 else None,
        effective_from=request.effective_from,
        effective_to=request.effective_to,
        created_by_user_id=actor_user_id,
        supersedes_id=supersedes_id,
    )
    db_session.add(policy)
    await db_session.flush()
    await append_business_event(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        actor_capacity="organization_admin",
        resource_kind="policy_version",
        resource_uuid=policy.policy_uuid,
        action="studytomy.policy.proposed",
        reason_code="POLICY_VERSION_PROPOSED",
        after_state={
            "policy_key": policy.policy_key,
            "version": policy.version,
            "status": policy.status,
            "effective_from": policy.effective_from.isoformat(),
            "effective_to": policy.effective_to.isoformat() if policy.effective_to else None,
        },
    )
    await complete_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        operation="studytomy.policy.propose",
        idempotency_key=idempotency_key,
        resource_kind="policy_version",
        resource_uuid=policy.policy_uuid,
        response_status=201,
        response_projection={"policy_uuid": policy.policy_uuid},
    )
    await db_session.commit()
    await db_session.refresh(policy)
    return await get_policy_version_read(db_session, organization, policy)


async def _active_owner_assignment(
    db_session: AsyncSession,
    organization: Organization,
    actor_user_id: int,
    responsibility: StudyOwnerFunction,
    now: datetime,
) -> StudyOwnerAssignment:
    result = await db_session.execute(
        select(StudyOwnerAssignment)
        .where(
            StudyOwnerAssignment.org_id == organization.id,
            StudyOwnerAssignment.user_id == actor_user_id,
            StudyOwnerAssignment.responsibility == responsibility.value,
            StudyOwnerAssignment.scope_kind == "organization",
            StudyOwnerAssignment.scope_uuid == organization.org_uuid,
            StudyOwnerAssignment.status == StudyOwnerStatus.ACTIVE.value,
        )
        .order_by(StudyOwnerAssignment.created_at.desc())
    )
    assignments = list(result.scalars().all())
    for assignment in assignments:
        if _as_utc(assignment.effective_from) <= now and (
            assignment.effective_to is None or _as_utc(assignment.effective_to) > now
        ):
            return assignment
    raise HTTPException(
        status_code=403,
        detail="An active, organization-scoped service-owner assignment is required",
    )


def _ranges_overlap(
    start_a: datetime,
    end_a: datetime | None,
    start_b: datetime,
    end_b: datetime | None,
) -> bool:
    start_a = _as_utc(start_a)
    start_b = _as_utc(start_b)
    end_a = _as_utc(end_a) if end_a is not None else None
    end_b = _as_utc(end_b) if end_b is not None else None
    return (end_a is None or start_b < end_a) and (end_b is None or start_a < end_b)


async def _ensure_no_approved_overlap(
    db_session: AsyncSession,
    organization: Organization,
    policy: StudyPolicyVersion,
    exclude_policy_id: int | None = None,
) -> None:
    result = await db_session.execute(
        select(StudyPolicyVersion).where(
            StudyPolicyVersion.org_id == organization.id,
            StudyPolicyVersion.policy_key == policy.policy_key,
            StudyPolicyVersion.scope_kind == policy.scope_kind,
            StudyPolicyVersion.scope_uuid == policy.scope_uuid,
            StudyPolicyVersion.status == StudyPolicyStatus.APPROVED.value,
        )
    )
    for existing in result.scalars().all():
        if existing.id == exclude_policy_id:
            continue
        if _ranges_overlap(
            policy.effective_from,
            policy.effective_to,
            existing.effective_from,
            existing.effective_to,
        ):
            _raise_conflict("An approved policy version already covers part of this effective period")


async def approve_policy_version(
    db_session: AsyncSession,
    organization: Organization,
    actor_user_id: int,
    policy_uuid: str,
    request: StudyPolicyApprovalRequest,
    idempotency_key: str | None = None,
) -> StudyPolicyVersionRead:
    await lock_users(db_session, [actor_user_id])
    await lock_organization(db_session, organization.id)
    await require_org_member_row(db_session, organization.id, actor_user_id)
    policy_result = await db_session.execute(
        select(StudyPolicyVersion)
        .where(
            StudyPolicyVersion.policy_uuid == policy_uuid,
            StudyPolicyVersion.org_id == organization.id,
        )
        .with_for_update()
    )
    policy = policy_result.scalars().first()
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy version not found")
    try:
        policy_key = StudyPolicyKey(policy.policy_key)
    except ValueError as error:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POLICY_BODY_INVALID",
                "message": "The policy key is not part of the decision register.",
            },
        ) from error
    body, body_blockers = inspect_study_policy_body(policy_key, policy.body)
    if body is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POLICY_BODY_INVALID",
                "message": "The policy body does not match its decision contract.",
            },
        )
    if any(body_blockers.values()):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POLICY_BODY_INCOMPLETE",
                "message": "Every decision area must be specified or evidenced as not applicable before approval.",
                "blockers": body_blockers,
            },
        )

    # Policy authors must not approve the same decision, even if they also
    # hold an owner assignment for one of its required responsibilities.
    if policy.created_by_user_id == actor_user_id:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "CONFLICT_OF_INTEREST_EXCLUDED",
                "message": "The policy author cannot approve their own policy version.",
            },
        )

    replay = await claim_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        operation="studytomy.policy.approve",
        idempotency_key=idempotency_key,
        request_payload={"policy_uuid": policy_uuid, "approval": request.model_dump(mode="json")},
    )
    if replay is not None:
        return await get_policy_version_read(db_session, organization, policy)
    if policy.status != StudyPolicyStatus.DRAFT.value:
        _raise_conflict("Only a draft policy version can receive an approval")

    required_responsibilities = POLICY_REQUIRED_RESPONSIBILITIES[policy_key]
    if request.responsibility not in required_responsibilities:
        raise HTTPException(status_code=422, detail="This responsibility is not an approver for the decision")

    now = datetime.now(timezone.utc)
    owner_assignment = await _active_owner_assignment(
        db_session,
        organization,
        actor_user_id,
        request.responsibility,
        now,
    )

    existing_approval_result = await db_session.execute(
        select(StudyPolicyApproval.responsibility).where(
            StudyPolicyApproval.org_id == organization.id,
            StudyPolicyApproval.policy_version_id == policy.id,
        )
    )
    recorded_responsibilities = set(existing_approval_result.scalars().all())
    if request.responsibility.value in recorded_responsibilities:
        _raise_conflict("This responsibility has already approved the policy version")
    required_values = {responsibility.value for responsibility in required_responsibilities}
    will_complete_approval = required_values.issubset(
        recorded_responsibilities | {request.responsibility.value}
    )
    previous = None
    if will_complete_approval:
        await _ensure_no_approved_overlap(
            db_session,
            organization,
            policy,
            policy.supersedes_id,
        )
        if policy.supersedes_id is not None:
            previous = await db_session.get(StudyPolicyVersion, policy.supersedes_id)
            if (
                previous is None
                or previous.org_id != organization.id
                or previous.policy_key != policy.policy_key
                or previous.scope_uuid != policy.scope_uuid
                or previous.status != StudyPolicyStatus.APPROVED.value
                or _as_utc(previous.effective_from) >= _as_utc(policy.effective_from)
            ):
                _raise_conflict("The referenced policy version can no longer be superseded")

    approval = StudyPolicyApproval(
        org_id=organization.id,
        policy_version_id=policy.id,
        owner_assignment_id=owner_assignment.id,
        responsibility=request.responsibility.value,
        approver_user_id=actor_user_id,
        evidence_reference=request.evidence_reference.strip(),
        rationale=request.rationale.strip(),
        approved_at=now,
    )
    db_session.add(approval)
    await db_session.flush()

    if will_complete_approval:
        if previous is not None:
            previous.status = StudyPolicyStatus.SUPERSEDED.value
            previous.superseded_at = now
            previous.supersession_evidence_reference = request.evidence_reference.strip()
            if previous.effective_to is None or _as_utc(previous.effective_to) > _as_utc(policy.effective_from):
                previous.effective_to = policy.effective_from
        policy.status = StudyPolicyStatus.APPROVED.value
        policy.approved_by_user_id = actor_user_id
        policy.approved_at = now

    await append_business_event(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        actor_capacity=request.responsibility.value,
        resource_kind="policy_version",
        resource_uuid=policy.policy_uuid,
        action="studytomy.policy.approval_recorded",
        reason_code="POLICY_APPROVAL_RECORDED",
        before_state={
            "policy_key": policy.policy_key,
            "version": policy.version,
            "status": StudyPolicyStatus.DRAFT.value,
        },
        after_state={
            "policy_key": policy.policy_key,
            "version": policy.version,
            "status": policy.status,
        },
        policy_refs=[policy.id],
    )
    await complete_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        operation="studytomy.policy.approve",
        idempotency_key=idempotency_key,
        resource_kind="policy_version",
        resource_uuid=policy.policy_uuid,
        response_status=200,
        response_projection={"policy_uuid": policy.policy_uuid},
    )
    await db_session.commit()
    await db_session.refresh(policy)
    return await get_policy_version_read(db_session, organization, policy)


async def supersede_policy_version(
    db_session: AsyncSession,
    organization: Organization,
    policy_uuid: str,
    actor_user_id: int,
    request: StudyPolicySupersedeRequest,
    idempotency_key: str | None = None,
) -> StudyPolicyVersionRead:
    await lock_users(db_session, [actor_user_id])
    await lock_organization(db_session, organization.id)
    await require_org_member_row(db_session, organization.id, actor_user_id)
    result = await db_session.execute(
        select(StudyPolicyVersion)
        .where(
            StudyPolicyVersion.policy_uuid == policy_uuid,
            StudyPolicyVersion.org_id == organization.id,
        )
        .with_for_update()
    )
    policy = result.scalars().first()
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy version not found")
    replay = await claim_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        operation="studytomy.policy.supersede",
        idempotency_key=idempotency_key,
        request_payload={
            "policy_uuid": policy_uuid,
            "supersession": request.model_dump(mode="json"),
        },
    )
    if replay is not None:
        return await get_policy_version_read(db_session, organization, policy)
    if policy.status != StudyPolicyStatus.APPROVED.value:
        _raise_conflict("Only an approved policy version can be superseded")

    required_responsibilities = POLICY_REQUIRED_RESPONSIBILITIES[
        StudyPolicyKey(policy.policy_key)
    ]
    assigned = False
    now = datetime.now(timezone.utc)
    for responsibility in required_responsibilities:
        try:
            await _active_owner_assignment(
                db_session,
                organization,
                actor_user_id,
                responsibility,
                now,
            )
            assigned = True
            break
        except HTTPException as error:
            if error.status_code != 403:
                raise
    if not assigned:
        raise HTTPException(
            status_code=403,
            detail="An active policy-owner assignment is required to supersede this decision",
        )

    before_state = {
        "policy_key": policy.policy_key,
        "version": policy.version,
        "status": policy.status,
        "effective_to": policy.effective_to.isoformat() if policy.effective_to else None,
    }
    policy.status = StudyPolicyStatus.SUPERSEDED.value
    policy.superseded_at = now
    policy.supersession_evidence_reference = request.evidence_reference.strip()
    if _as_utc(policy.effective_from) < now and (
        policy.effective_to is None or _as_utc(policy.effective_to) > now
    ):
        policy.effective_to = now
    await append_business_event(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        actor_capacity="policy_owner",
        resource_kind="policy_version",
        resource_uuid=policy.policy_uuid,
        action="studytomy.policy.superseded",
        reason_code="POLICY_VERSION_SUPERSEDED",
        before_state=before_state,
        after_state={
            "policy_key": policy.policy_key,
            "version": policy.version,
            "status": policy.status,
            "effective_to": policy.effective_to.isoformat() if policy.effective_to else None,
        },
        policy_refs=[policy.id],
    )
    await complete_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor_user_id,
        operation="studytomy.policy.supersede",
        idempotency_key=idempotency_key,
        resource_kind="policy_version",
        resource_uuid=policy.policy_uuid,
        response_status=200,
        response_projection={"policy_uuid": policy.policy_uuid},
    )
    await db_session.commit()
    await db_session.refresh(policy)
    return await get_policy_version_read(db_session, organization, policy)


async def list_policy_versions(
    db_session: AsyncSession,
    organization: Organization,
    actor_user_id: int,
    include_all: bool,
    limit: int = 25,
) -> list[StudyPolicyVersionRead]:
    statement = select(StudyPolicyVersion).where(
        StudyPolicyVersion.org_id == organization.id
    )
    if not include_all:
        now = datetime.now(timezone.utc)
        owner_result = await db_session.execute(
            select(StudyOwnerAssignment).where(
                StudyOwnerAssignment.org_id == organization.id,
                StudyOwnerAssignment.user_id == actor_user_id,
                StudyOwnerAssignment.scope_uuid == organization.org_uuid,
                StudyOwnerAssignment.status == StudyOwnerStatus.ACTIVE.value,
            )
        )
        responsibilities = {
            assignment.responsibility
            for assignment in owner_result.scalars().all()
            if _as_utc(assignment.effective_from) <= now
            and (assignment.effective_to is None or _as_utc(assignment.effective_to) > now)
        }
        permitted_keys = [
            policy_key.value
            for policy_key, required in POLICY_REQUIRED_RESPONSIBILITIES.items()
            if any(responsibility.value in responsibilities for responsibility in required)
        ]
        if not permitted_keys:
            raise HTTPException(
                status_code=403,
                detail="An active, organization-scoped service-owner assignment is required",
            )
        statement = statement.where(StudyPolicyVersion.policy_key.in_(permitted_keys))

    result = await db_session.execute(
        statement.order_by(StudyPolicyVersion.created_at.desc(), StudyPolicyVersion.id.desc())
        .limit(limit)
    )
    versions = list(result.scalars().all())
    return [await get_policy_version_read(db_session, organization, version) for version in versions]


async def get_policy_version_read(
    db_session: AsyncSession,
    organization: Organization,
    policy: StudyPolicyVersion,
) -> StudyPolicyVersionRead:
    creator = await db_session.get(User, policy.created_by_user_id)
    approvals_result = await db_session.execute(
        select(StudyPolicyApproval)
        .where(
            StudyPolicyApproval.org_id == organization.id,
            StudyPolicyApproval.policy_version_id == policy.id,
        )
        .order_by(StudyPolicyApproval.approved_at, StudyPolicyApproval.id)
    )
    approvals: list[StudyPolicyApprovalRead] = []
    for approval in approvals_result.scalars().all():
        approver = await db_session.get(User, approval.approver_user_id)
        if approver is None:
            raise HTTPException(status_code=409, detail="Policy approver identity is unavailable")
        approvals.append(
            StudyPolicyApprovalRead(
                responsibility=StudyOwnerFunction(approval.responsibility),
                approver_user_uuid=approver.user_uuid,
                evidence_reference=approval.evidence_reference,
                rationale=approval.rationale,
                approved_at=approval.approved_at,
            )
        )

    previous = (
        await db_session.get(StudyPolicyVersion, policy.supersedes_id)
        if policy.supersedes_id is not None
        else None
    )
    if creator is None:
        raise HTTPException(status_code=409, detail="Policy creator record is unavailable")
    body, _ = inspect_study_policy_body(policy.policy_key, policy.body)
    if body is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POLICY_BODY_INVALID",
                "message": "The stored policy body does not match its decision contract.",
            },
        )
    return StudyPolicyVersionRead(
        policy_uuid=policy.policy_uuid,
        policy_key=StudyPolicyKey(policy.policy_key),
        scope_kind=policy.scope_kind,
        scope_uuid=policy.scope_uuid,
        version=policy.version,
        status=StudyPolicyStatus(policy.status),
        body=body,
        evidence_reference=policy.evidence_reference,
        evidence_sha256=policy.evidence_sha256,
        effective_from=policy.effective_from,
        effective_to=policy.effective_to,
        created_by_user_uuid=creator.user_uuid,
        approved_at=policy.approved_at,
        supersedes_policy_uuid=previous.policy_uuid if previous else None,
        created_at=policy.created_at,
        approvals=approvals,
        required_responsibilities=sorted(
            POLICY_REQUIRED_RESPONSIBILITIES[StudyPolicyKey(policy.policy_key)],
            key=lambda responsibility: responsibility.value,
        ),
    )
