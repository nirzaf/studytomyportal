"""Organization-scoped endpoints for explicit Studytomy governance decisions."""

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.user_organizations import UserOrganization
from src.db.studytomy.governance import (
    StudyOwnerAssignmentCreate,
    StudyOwnerAssignment,
    StudyStaffScope,
    StudyOwnerAssignmentRead,
    StudyPolicyApprovalRequest,
    StudyPolicyProposal,
    StudyPolicySupersedeRequest,
    StudyPolicyVersionRead,
    StudyStaffScopeCreate,
    StudyStaffScopeRead,
    StudyStaffScopeRevoke,
    StudyFinancialDelegationCreate,
    StudyFinancialDelegationRead,
)
from src.db.users import PublicUser
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.security.org_auth import require_org_admin, require_org_membership
from src.services.studytomy.policies import (
    approve_policy_version,
    create_owner_assignment,
    list_owner_assignments,
    list_policy_versions,
    propose_policy_version,
    create_staff_scope,
    list_staff_scopes,
    revoke_staff_scope,
    set_financial_delegation,
    supersede_policy_version,
)
from src.services.studytomy.readiness import (
    StudyOfferKind,
    StudyReadinessRead,
    evaluate_course_offer_readiness,
)
from src.security.rbac.constants import ADMIN_OR_MAINTAINER_ROLE_IDS
from src.security.studytomy_access import StudytomyCapabilityName, evaluate_studytomy_staff_access
from src.services.studytomy.cases import _require_safeguarding_triage
from src.services.studytomy.support import _staff_context


router = APIRouter()


async def _resolve_org(
    org_uuid: str,
    current_user: PublicUser,
    db_session: AsyncSession,
    *,
    admin_only: bool,
) -> Organization:
    result = await db_session.execute(
        select(Organization).where(Organization.org_uuid == org_uuid)
    )
    organization = result.scalars().first()
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    if admin_only:
        await require_org_admin(current_user.id, organization.id, db_session)
    else:
        await require_org_membership(current_user.id, organization.id, db_session)
    membership = await db_session.execute(
        select(UserOrganization.role_id).where(
            UserOrganization.user_id == current_user.id,
            UserOrganization.org_id == organization.id,
        )
    )
    role_id = membership.scalar_one_or_none()
    if role_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="An explicit organization membership is required for Studytomy governance",
        )
    if admin_only and role_id not in ADMIN_OR_MAINTAINER_ROLE_IDS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization administration is required for this action",
        )
    return organization


@router.get("/orgs/{org_uuid}/me/staff-workspaces")
async def api_my_staff_workspaces(
    org_uuid: str, response: Response,
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> list[str]:
    org = await _resolve_org(org_uuid, current_user, db_session, admin_only=False)
    response.headers["Cache-Control"] = "private, no-store"
    workspaces = set()
    for workspace, authority in (("cases", _require_safeguarding_triage), ("support-cases", _staff_context)):
        try:
            await authority(db_session, org, current_user)
        except HTTPException as error:
            if error.status_code not in (403, 404, 409):
                raise
        else:
            workspaces.add(workspace)
    capability = StudytomyCapabilityName
    mappings = {
        (capability.FAMILY_AUTHORITY_REVIEW, "learner"): "families",
        (capability.SAFEGUARDING_CASE_REVIEW, "case"): "cases",
        (capability.FINANCIAL_RECONCILIATION, "collection"): "finance",
        (capability.REFUND_APPROVAL, "order"): "finance",
        (capability.PRIVACY_RETENTION_REVIEW, "case"): "privacy-reviews",
        (capability.SUPPORT_OPERATING_REPORT, "function"): "support-cases",
    }
    names = {name.value for name, _ in mappings}
    rows = (await db_session.execute(
        select(StudyStaffScope.capability, StudyStaffScope.scope_kind, StudyStaffScope.scope_uuid)
        .join(StudyOwnerAssignment, StudyOwnerAssignment.id == StudyStaffScope.assignment_id)
        .where(
            StudyStaffScope.org_id == org.id, StudyOwnerAssignment.org_id == org.id,
            StudyOwnerAssignment.user_id == current_user.id, StudyOwnerAssignment.status == "active",
            StudyStaffScope.status == "active", StudyStaffScope.is_exclusion.is_(False),
            StudyStaffScope.capability.in_(names | {f"studytomy.{name}" for name in names}),
        ).distinct()
    )).all()
    for name, kind, reference in rows:
        name = capability(name.removeprefix("studytomy."))
        workspace = mappings.get((name, kind))
        if workspace is None or workspace in workspaces:
            continue
        decision = await evaluate_studytomy_staff_access(
            db_session, current_user, org, name, target_scope_kind=kind, target_resource_uuid=reference,
        )
        if decision.allowed:
            workspaces.add(workspace)
    return sorted(workspaces)


@router.get(
    "/orgs/{org_uuid}/courses/{course_uuid}/readiness",
    response_model=StudyReadinessRead,
    summary="Recompute course offer readiness blockers",
)
async def api_course_offer_readiness(
    org_uuid: str,
    course_uuid: str,
    offer_kind: StudyOfferKind = Query(default=StudyOfferKind.COURSE),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyReadinessRead:
    organization = await _resolve_org(
        org_uuid,
        current_user,
        db_session,
        admin_only=True,
    )
    return await evaluate_course_offer_readiness(
        db_session,
        organization,
        course_uuid,
        offer_kind,
    )


@router.post(
    "/orgs/{org_uuid}/service-owners",
    response_model=StudyOwnerAssignmentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Assign an accountable Studytomy service owner",
)
async def api_create_service_owner(
    org_uuid: str,
    request: StudyOwnerAssignmentCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyOwnerAssignmentRead:
    organization = await _resolve_org(
        org_uuid, current_user, db_session, admin_only=True
    )
    return await create_owner_assignment(
        db_session, organization, current_user.id, request, idempotency_key
    )


@router.get(
    "/orgs/{org_uuid}/service-owners",
    response_model=list[StudyOwnerAssignmentRead],
    summary="List accountable Studytomy service owners",
)
async def api_list_service_owners(
    org_uuid: str,
    limit: int = Query(default=25, ge=1, le=100),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> list[StudyOwnerAssignmentRead]:
    organization = await _resolve_org(
        org_uuid, current_user, db_session, admin_only=True
    )
    return await list_owner_assignments(db_session, organization, limit)


@router.post(
    "/orgs/{org_uuid}/service-owners/{owner_uuid}/scopes",
    response_model=StudyStaffScopeRead,
    status_code=status.HTTP_201_CREATED,
    summary="Grant an evidenced resource-scoped Studytomy capability",
)
async def api_create_staff_scope(
    org_uuid: str,
    owner_uuid: str,
    request: StudyStaffScopeCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyStaffScopeRead:
    organization = await _resolve_org(
        org_uuid, current_user, db_session, admin_only=True
    )
    return await create_staff_scope(
        db_session, organization, current_user.id, owner_uuid, request, idempotency_key
    )


@router.get(
    "/orgs/{org_uuid}/service-owners/{owner_uuid}/scopes",
    response_model=list[StudyStaffScopeRead],
    summary="List resource-scoped Studytomy capabilities",
)
async def api_list_staff_scopes(
    org_uuid: str,
    owner_uuid: str,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> list[StudyStaffScopeRead]:
    organization = await _resolve_org(
        org_uuid, current_user, db_session, admin_only=True
    )
    return await list_staff_scopes(db_session, organization, owner_uuid, limit)


@router.post(
    "/orgs/{org_uuid}/staff-scopes/{scope_record_uuid}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an evidenced Studytomy resource scope",
)
async def api_revoke_staff_scope(
    org_uuid: str,
    scope_record_uuid: str,
    request: StudyStaffScopeRevoke,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> None:
    organization = await _resolve_org(
        org_uuid, current_user, db_session, admin_only=True
    )
    await revoke_staff_scope(
        db_session, organization, current_user.id, scope_record_uuid, request, idempotency_key
    )


@router.put(
    "/orgs/{org_uuid}/service-owners/{owner_uuid}/financial-delegation",
    response_model=StudyFinancialDelegationRead,
    summary="Set a reasoned financial delegation under approved D-19",
)
async def api_set_financial_delegation(
    org_uuid: str,
    owner_uuid: str,
    request: StudyFinancialDelegationCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyFinancialDelegationRead:
    organization = await _resolve_org(
        org_uuid, current_user, db_session, admin_only=True
    )
    return await set_financial_delegation(
        db_session, organization, current_user.id, owner_uuid, request, idempotency_key
    )


@router.post(
    "/orgs/{org_uuid}/policies",
    response_model=StudyPolicyVersionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Propose a draft Studytomy policy version",
)
async def api_propose_policy(
    org_uuid: str,
    request: StudyPolicyProposal,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyPolicyVersionRead:
    organization = await _resolve_org(
        org_uuid, current_user, db_session, admin_only=True
    )
    return await propose_policy_version(
        db_session, organization, current_user.id, request, idempotency_key
    )


@router.get(
    "/orgs/{org_uuid}/policies",
    response_model=list[StudyPolicyVersionRead],
    summary="List Studytomy policy versions",
)
async def api_list_policies(
    org_uuid: str,
    limit: int = Query(default=25, ge=1, le=100),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> list[StudyPolicyVersionRead]:
    # Resolve the requested organization before checking whether this is an
    # administrator view or a service-owner-only view.
    organization_result = await db_session.execute(
        select(Organization).where(Organization.org_uuid == org_uuid)
    )
    organization = organization_result.scalars().first()
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    await require_org_membership(current_user.id, organization.id, db_session)
    membership = await db_session.execute(
        select(UserOrganization.role_id).where(
            UserOrganization.user_id == current_user.id,
            UserOrganization.org_id == organization.id,
        )
    )
    role_id = membership.scalar_one_or_none()
    if role_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="An explicit organization membership is required for Studytomy governance",
        )
    include_all = role_id in ADMIN_OR_MAINTAINER_ROLE_IDS
    if include_all:
        await require_org_admin(current_user.id, organization.id, db_session)
    return await list_policy_versions(
        db_session,
        organization,
        current_user.id,
        include_all,
        limit,
    )


@router.post(
    "/orgs/{org_uuid}/policies/{policy_uuid}/approvals",
    response_model=StudyPolicyVersionRead,
    summary="Record a named owner approval for a draft policy version",
)
async def api_approve_policy(
    org_uuid: str,
    policy_uuid: str,
    request: StudyPolicyApprovalRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyPolicyVersionRead:
    organization = await _resolve_org(
        org_uuid, current_user, db_session, admin_only=False
    )
    return await approve_policy_version(
        db_session, organization, current_user.id, policy_uuid, request, idempotency_key
    )


@router.post(
    "/orgs/{org_uuid}/policies/{policy_uuid}/supersede",
    response_model=StudyPolicyVersionRead,
    summary="Supersede an approved policy version with recorded evidence",
)
async def api_supersede_policy(
    org_uuid: str,
    policy_uuid: str,
    request: StudyPolicySupersedeRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyPolicyVersionRead:
    organization = await _resolve_org(
        org_uuid, current_user, db_session, admin_only=False
    )
    return await supersede_policy_version(
        db_session,
        organization,
        policy_uuid,
        current_user.id,
        request,
        idempotency_key,
    )
