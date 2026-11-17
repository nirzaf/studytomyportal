"""Service-boundary authorization for Studytomy staff capabilities, scopes, and conflict exclusions (TASK-003).

Enforces the non-negotiable access invariants from §5.2–5.3 and TASK-003:
1. Default-deny Studytomy action capabilities on `Rights.studytomy`; never infer
   unrestricted safeguarding, family-authority, learner-dossier, or financial
   approval power from generic Admin teaching rights (`Role.id in (1, 2)` or
   `is_superadmin`) or client-supplied role labels.
2. Reject `AnonymousUser` (401) and `APITokenUser` (403) for staff/family/financial
   decisions.
3. Require BOTH organization membership + explicit capability AND active
   organization-scoped `StudyOwnerAssignment` (when an accountable owner function is
   required) AND assigned resource/case scope (when operating on a learner dossier,
   course, or case).
4. Conflict-of-interest and self-approval exclusion take unconditional precedence:
   the subject of a complaint/case or the subject/requester of an approval cannot
   review or approve it, even if they hold an Admin role and active owner assignment.
5. Payer linkage never grants access to learner marks or academic records.
6. Financial delegation checks validate currency, minor-unit ceiling, policy version
   references, non-blank reason, and non-self beneficiary.
"""

from collections.abc import Collection
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, field_validator
from pydantic import Field as PydanticField
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.roles import Rights, Role, RoleTypeEnum, StudytomyCapabilities
from src.db.studytomy.governance import (
    StudyOwnerAssignment,
    StudyOwnerFunction,
    StudyOwnerStatus,
    StudyPolicyStatus,
    StudyPolicyVersion,
    StudyStaffScope,
    inspect_study_policy_body,
)
from src.db.user_organizations import UserOrganization
from src.db.users import AnonymousUser, APITokenUser, PublicUser, User


class StudytomyCapabilityName(str, Enum):
    FAMILY_AUTHORITY_REVIEW = "family_authority_review"
    LEARNER_ELIGIBILITY_REVIEW = "learner_eligibility_review"
    LEARNER_DOSSIER_READ = "learner_dossier_read"
    SAFEGUARDING_CASE_REVIEW = "safeguarding_case_review"
    FINANCIAL_RECONCILIATION = "financial_reconciliation"
    REFUND_APPROVAL = "refund_approval"
    OFFER_PUBLICATION = "offer_publication"
    CONTENT_RELEASE_REVIEW = "content_release_review"
    ADAPTIVE_CURRICULUM_AUTHOR = "adaptive_curriculum_author"
    ASSESSMENT_MODERATION = "assessment_moderation"
    PROGRESS_REPORT_REVIEW = "progress_report_review"
    PRIVACY_RETENTION_REVIEW = "privacy_retention_review"


_REQUIRED_RESPONSIBILITY: dict[StudytomyCapabilityName, StudyOwnerFunction] = {
    StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW: StudyOwnerFunction.FAMILY_SERVICES,
    StudytomyCapabilityName.LEARNER_ELIGIBILITY_REVIEW: StudyOwnerFunction.FAMILY_SERVICES,
    StudytomyCapabilityName.LEARNER_DOSSIER_READ: StudyOwnerFunction.EDUCATION,
    StudytomyCapabilityName.SAFEGUARDING_CASE_REVIEW: StudyOwnerFunction.SAFEGUARDING,
    StudytomyCapabilityName.FINANCIAL_RECONCILIATION: StudyOwnerFunction.COMMERCIAL,
    StudytomyCapabilityName.REFUND_APPROVAL: StudyOwnerFunction.COMMERCIAL,
    StudytomyCapabilityName.OFFER_PUBLICATION: StudyOwnerFunction.BUSINESS,
    StudytomyCapabilityName.CONTENT_RELEASE_REVIEW: StudyOwnerFunction.EDUCATION,
    StudytomyCapabilityName.ADAPTIVE_CURRICULUM_AUTHOR: StudyOwnerFunction.EDUCATION,
    StudytomyCapabilityName.ASSESSMENT_MODERATION: StudyOwnerFunction.EDUCATION,
    StudytomyCapabilityName.PROGRESS_REPORT_REVIEW: StudyOwnerFunction.EDUCATION,
    StudytomyCapabilityName.PRIVACY_RETENTION_REVIEW: StudyOwnerFunction.PRIVACY,
}


class StudytomyAccessDecision(BaseModel):
    """Proposed authorization result contract (§5.2).

    Public messages never expose confidential case narratives or other adults' data.
    """

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    code: str
    scope: Optional[str] = None
    policy_version_ids: list[int] = PydanticField(default_factory=list)
    access_version: int = PydanticField(default=1, ge=1)
    next_action: Optional[str] = None
    public_message: str


class FinancialDelegationLimit(BaseModel):
    """Reasoned financial delegation limit for refund/adjustment approvals (TASK-003, BR-PAY-08, D-19)."""

    model_config = ConfigDict(extra="forbid")

    currency: str = PydanticField(min_length=3, max_length=3)
    max_amount_minor: int = PydanticField(ge=0)
    policy_version_id: int = PydanticField(ge=1)
    evidence_reference: str = PydanticField(min_length=1, max_length=500)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if len(cleaned) != 3 or not cleaned.isalpha():
            raise ValueError("Currency must be a 3-letter ISO code")
        return cleaned

    @field_validator("evidence_reference")
    @classmethod
    def require_nonblank_evidence(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Delegation evidence reference cannot be blank")
        return value.strip()


def _extract_studytomy_capabilities(role: Role) -> StudytomyCapabilities:
    """Safely extract `StudytomyCapabilities` from `Role.rights` (dict or `Rights`),
    defaulting all Studytomy capabilities to `False` when absent.
    """
    raw_rights = role.rights
    if isinstance(raw_rights, Rights):
        return raw_rights.studytomy
    if isinstance(raw_rights, dict):
        studytomy_block = raw_rights.get("studytomy")
        if isinstance(studytomy_block, StudytomyCapabilities):
            return studytomy_block
        if isinstance(studytomy_block, dict):
            return StudytomyCapabilities.model_validate(studytomy_block)
    return StudytomyCapabilities()


async def _find_active_owner_assignment(
    db_session: AsyncSession,
    org_id: int,
    org_uuid: str,
    user_id: int,
    responsibility: StudyOwnerFunction,
    now: datetime,
) -> Optional[StudyOwnerAssignment]:
    stmt = (
        select(StudyOwnerAssignment)
        .where(
            StudyOwnerAssignment.org_id == org_id,
            StudyOwnerAssignment.user_id == user_id,
            StudyOwnerAssignment.responsibility == responsibility.value,
            StudyOwnerAssignment.scope_kind == "organization",
            StudyOwnerAssignment.scope_uuid == org_uuid,
            StudyOwnerAssignment.status == StudyOwnerStatus.ACTIVE.value,
            StudyOwnerAssignment.effective_from <= now,
        )
        .order_by(StudyOwnerAssignment.created_at.desc(), StudyOwnerAssignment.id.desc())
    )
    for assignment in (await db_session.execute(stmt)).scalars().all():
        ef_to = assignment.effective_to
        if ef_to is not None and ef_to.tzinfo is None:
            ef_to = ef_to.replace(tzinfo=timezone.utc)
        if ef_to is None or ef_to > now:
            return assignment
    return None


def _required_policy_keys(capability: StudytomyCapabilityName) -> tuple[str, ...]:
    """Return policy gates that are already required by the source specification."""
    if capability == StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW:
        return ("D-03", "D-12")
    if capability == StudytomyCapabilityName.LEARNER_DOSSIER_READ:
        return ("D-03",)
    if capability == StudytomyCapabilityName.LEARNER_ELIGIBILITY_REVIEW:
        return ("D-01", "D-03", "D-12")
    if capability == StudytomyCapabilityName.SAFEGUARDING_CASE_REVIEW:
        return ("D-04", "D-11", "D-12", "D-13")
    if capability in {
        StudytomyCapabilityName.FINANCIAL_RECONCILIATION,
        StudytomyCapabilityName.REFUND_APPROVAL,
    }:
        return ("D-19",)
    if capability == StudytomyCapabilityName.PRIVACY_RETENTION_REVIEW:
        return ("D-12", "D-13", "D-15")
    if capability == StudytomyCapabilityName.CONTENT_RELEASE_REVIEW:
        return ("D-02", "D-10", "D-15", "D-16")
    return ()


async def _active_approved_policy_ids(
    db_session: AsyncSession,
    organization: Organization,
    policy_keys: Collection[str],
    now: datetime,
) -> tuple[list[int] | None, str | None]:
    """Return active policy IDs and a fail-closed reason for any missing decision."""
    policy_ids: list[int] = []
    for policy_key in policy_keys:
        result = await db_session.execute(
            select(StudyPolicyVersion)
            .where(
                StudyPolicyVersion.org_id == organization.id,
                StudyPolicyVersion.policy_key == policy_key,
                StudyPolicyVersion.scope_kind == "organization",
                StudyPolicyVersion.scope_uuid == organization.org_uuid,
                StudyPolicyVersion.status == StudyPolicyStatus.APPROVED.value,
                StudyPolicyVersion.effective_from <= now,
            )
            .order_by(StudyPolicyVersion.version.desc())
        )
        active = None
        for candidate in result.scalars().all():
            ends = candidate.effective_to
            if ends is None or _as_utc(ends) > now:
                active = candidate
                break
        if active is None:
            return None, "POLICY_NOT_APPROVED"
        body, blockers = inspect_study_policy_body(policy_key, active.body)
        if body is None:
            return None, "POLICY_BODY_INVALID"
        if any(blockers.values()):
            return None, "POLICY_BODY_INCOMPLETE"
        policy_ids.append(int(active.id))
    return policy_ids, None


async def _find_active_staff_scope(
    db_session: AsyncSession,
    org_id: int,
    user_id: int,
    responsibility: Optional[StudyOwnerFunction],
    capability: StudytomyCapabilityName,
    target_scope_kind: str,
    target_scope_uuid: str,
    organization_uuid: str,
    now: datetime,
) -> tuple[Optional[StudyOwnerAssignment], bool]:
    """Resolve the assignment and exact persisted scope; exclusions win over grants."""
    owner_query = select(StudyOwnerAssignment).where(
        StudyOwnerAssignment.org_id == org_id,
        StudyOwnerAssignment.user_id == user_id,
        StudyOwnerAssignment.scope_kind == "organization",
        StudyOwnerAssignment.scope_uuid == organization_uuid,
        StudyOwnerAssignment.status == StudyOwnerStatus.ACTIVE.value,
        StudyOwnerAssignment.effective_from <= now,
    )
    if responsibility is not None:
        owner_query = owner_query.where(
            StudyOwnerAssignment.responsibility == responsibility.value
        )
    owner_query = owner_query.order_by(
        StudyOwnerAssignment.created_at.desc(), StudyOwnerAssignment.id.desc()
    )
    owner_rows = (await db_session.execute(owner_query)).scalars().all()
    active_owners = [
        owner
        for owner in owner_rows
        if owner.effective_to is None or _as_utc(owner.effective_to) > now
    ]
    if not active_owners:
        return None, False

    owner_ids = [owner.id for owner in active_owners]
    canonical_capability = capability.value
    capability_alias = f"studytomy.{canonical_capability}"
    scopes = (
        await db_session.execute(
            select(StudyStaffScope).where(
                StudyStaffScope.org_id == org_id,
                StudyStaffScope.assignment_id.in_(owner_ids),
                StudyStaffScope.capability.in_((canonical_capability, capability_alias)),
                StudyStaffScope.status == "active",
            )
        )
    ).scalars().all()
    applicable = [
        scope
        for scope in scopes
        if (scope.scope_kind, scope.scope_uuid)
        in {
            (target_scope_kind, target_scope_uuid),
            ("organization", None),
        }
    ]
    if any(scope.is_exclusion for scope in applicable):
        return active_owners[0], False

    # Sensitive learner, case and finance actions require an exact resource grant;
    # an organization-wide role or scope never becomes a dossier/case/finance ACL.
    has_exact_grant = any(
        not scope.is_exclusion
        and scope.scope_kind == target_scope_kind
        and scope.scope_uuid == target_scope_uuid
        for scope in applicable
    )
    if not has_exact_grant:
        return active_owners[0], False
    for owner in active_owners:
        if any(
            scope.assignment_id == owner.id
            and not scope.is_exclusion
            and scope.scope_kind == target_scope_kind
            and scope.scope_uuid == target_scope_uuid
            for scope in applicable
        ):
            return owner, True
    return active_owners[0], False


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def evaluate_studytomy_staff_access(
    db_session: AsyncSession,
    current_user: PublicUser | User | AnonymousUser | APITokenUser,
    organization: Organization,
    capability: StudytomyCapabilityName,
    *,
    required_responsibility: Optional[StudyOwnerFunction] = None,
    target_resource_uuid: Optional[str] = None,
    target_scope_kind: Optional[str] = None,
    subject_user_id: Optional[int] = None,
    excluded_user_ids: Optional[Collection[int]] = None,
    access_version: int = 1,
    now: Optional[datetime] = None,
) -> StudytomyAccessDecision:
    """Evaluate staff capability + organization membership + owner/resource scope + conflict exclusion.

    Never raises on normal policy/permission denials; callers can inspect the structured
    `StudytomyAccessDecision` or call `authorize_studytomy_staff_action` to raise HTTP errors.
    """
    resolved_policy_ids: list[int] = []

    if isinstance(current_user, AnonymousUser):
        return StudytomyAccessDecision(
            allowed=False,
            code="AUTHENTICATION_REQUIRED",
            scope=None,
            policy_version_ids=resolved_policy_ids,
            access_version=access_version,
            next_action="Sign in with an authorized staff account",
            public_message="Authentication is required to perform this action.",
        )

    if isinstance(current_user, APITokenUser):
        return StudytomyAccessDecision(
            allowed=False,
            code="API_TOKEN_FORBIDDEN",
            scope=None,
            policy_version_ids=resolved_policy_ids,
            access_version=access_version,
            next_action="Use an interactive authenticated staff session",
            public_message="API tokens are not permitted for Studytomy staff or family operations.",
        )

    actor_id = int(current_user.id)

    # 1. Conflict-of-interest and self-approval exclusion come BEFORE any role/owner check
    # so that even a privileged administrator or active service owner cannot review a case
    # in which they are a named subject or approve their own request.
    if subject_user_id is not None and actor_id == int(subject_user_id):
        return StudytomyAccessDecision(
            allowed=False,
            code="CONFLICT_OF_INTEREST_EXCLUDED",
            scope=None,
            policy_version_ids=resolved_policy_ids,
            access_version=access_version,
            next_action="Escalate to an independent assigned reviewer",
            public_message="You cannot review or approve an action concerning yourself.",
        )

    if excluded_user_ids is not None and actor_id in {int(uid) for uid in excluded_user_ids}:
        return StudytomyAccessDecision(
            allowed=False,
            code="CONFLICT_OF_INTEREST_EXCLUDED",
            scope=None,
            policy_version_ids=resolved_policy_ids,
            access_version=access_version,
            next_action="Escalate to an independent assigned reviewer",
            public_message="This record requires an independent reviewer.",
        )

    # 2. Organization membership check (superadmin flag does NOT bypass org membership
    # or grant implicit safeguarding/family/financial authority).
    membership_stmt = select(UserOrganization, Role).join(
        Role, UserOrganization.role_id == Role.id
    ).where(
        UserOrganization.user_id == actor_id,
        UserOrganization.org_id == organization.id,
    )
    membership_row = (await db_session.execute(membership_stmt)).first()
    if membership_row is None:
        return StudytomyAccessDecision(
            allowed=False,
            code="ORG_SCOPE_DENIED",
            scope=None,
            policy_version_ids=resolved_policy_ids,
            access_version=access_version,
            next_action="Verify organization context",
            public_message="You do not have access to this organization scope.",
        )

    _, role = membership_row
    capabilities = _extract_studytomy_capabilities(role)

    # 3. Default-deny capability check on `Rights.studytomy`
    if (
        role.org_id != organization.id
        or role.role_type != RoleTypeEnum.TYPE_ORGANIZATION
        or not bool(capabilities[capability.value])
    ):
        return StudytomyAccessDecision(
            allowed=False,
            code="CAPABILITY_DENIED",
            scope=None,
            policy_version_ids=resolved_policy_ids,
            access_version=access_version,
            next_action="Request the required Studytomy role capability from an authorized owner",
            public_message="Your role does not grant the required capability for this action.",
        )

    # Sensitive service actions always require a concrete resource/case assignment.
    scope_kind = target_scope_kind or {
        StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW: "learner",
        StudytomyCapabilityName.LEARNER_ELIGIBILITY_REVIEW: "learner",
        StudytomyCapabilityName.LEARNER_DOSSIER_READ: "learner",
        StudytomyCapabilityName.SAFEGUARDING_CASE_REVIEW: "case",
        StudytomyCapabilityName.FINANCIAL_RECONCILIATION: "collection",
        StudytomyCapabilityName.REFUND_APPROVAL: "order",
        StudytomyCapabilityName.OFFER_PUBLICATION: "offer",
        StudytomyCapabilityName.CONTENT_RELEASE_REVIEW: "course",
        StudytomyCapabilityName.ADAPTIVE_CURRICULUM_AUTHOR: "course",
        StudytomyCapabilityName.ASSESSMENT_MODERATION: "course",
        StudytomyCapabilityName.PROGRESS_REPORT_REVIEW: "learner",
        StudytomyCapabilityName.PRIVACY_RETENTION_REVIEW: "case",
    }[capability]
    if not target_resource_uuid:
        return StudytomyAccessDecision(
            allowed=False,
            code="RESOURCE_SCOPE_REQUIRED",
            scope=None,
            policy_version_ids=resolved_policy_ids,
            access_version=access_version,
            next_action="Specify the resource or case assigned for this action",
            public_message="An explicit resource assignment is required for this operation.",
        )

    # Only database-approved policy versions can satisfy decision gates. Caller-supplied
    # IDs and client-side role flags never activate an unresolved D-03 or D-19 policy.
    effective_now = now or datetime.now(timezone.utc)
    required_keys = _required_policy_keys(capability)
    approved_policy_ids, policy_error = await _active_approved_policy_ids(
        db_session, organization, required_keys, effective_now
    )
    if approved_policy_ids is None:
        policy_error = policy_error or "POLICY_NOT_APPROVED"
        is_body_problem = policy_error != "POLICY_NOT_APPROVED"
        return StudytomyAccessDecision(
            allowed=False,
            code=policy_error,
            scope=None,
            policy_version_ids=[],
            access_version=access_version,
            next_action=(
                "Propose a complete policy version and obtain the required named-owner approvals"
                if is_body_problem
                else "Obtain approval for the governing Studytomy decision before enabling this action"
            ),
            public_message=(
                "The approved policy record is incomplete or invalid."
                if is_body_problem
                else "The policy required for this operation has not been approved."
            ),
        )
    resolved_policy_ids.extend(approved_policy_ids)

    enforced_responsibility = _REQUIRED_RESPONSIBILITY[capability]
    if required_responsibility is not None and required_responsibility != enforced_responsibility:
        return StudytomyAccessDecision(
            allowed=False,
            code="OWNER_FUNCTION_MISMATCH",
            scope=None,
            policy_version_ids=resolved_policy_ids,
            access_version=access_version,
            next_action="Use the accountable service-owner function assigned to this capability",
            public_message="This operation requires a different accountable service-owner function.",
        )
    owner_assignment, has_scope = await _find_active_staff_scope(
        db_session,
        int(organization.id),
        actor_id,
        enforced_responsibility,
        capability,
        scope_kind,
        target_resource_uuid,
        organization.org_uuid,
        effective_now,
    )
    if owner_assignment is None:
        return StudytomyAccessDecision(
            allowed=False,
            code="OWNER_ASSIGNMENT_REQUIRED",
            scope=None,
            policy_version_ids=resolved_policy_ids,
            access_version=access_version,
            next_action="Assign an active accountable service owner for this operation",
            public_message="An active service-owner assignment is required for this operation.",
        )
    if not has_scope:
        return StudytomyAccessDecision(
            allowed=False,
            code="RESOURCE_SCOPE_UNASSIGNED",
            scope=None,
            policy_version_ids=resolved_policy_ids,
            access_version=access_version,
            next_action="Request assignment to the target course, learner, collection, order, or case",
            public_message="You are not assigned to the requested resource scope.",
        )

    scope_label = (
        f"org:{organization.org_uuid}:resource:{target_resource_uuid}"
        if target_resource_uuid
        else f"org:{organization.org_uuid}"
    )
    return StudytomyAccessDecision(
        allowed=True,
        code="ALLOWED",
        scope=scope_label,
        policy_version_ids=resolved_policy_ids,
        access_version=access_version,
        next_action=None,
        public_message="Access granted.",
    )


async def authorize_studytomy_staff_action(
    db_session: AsyncSession,
    current_user: PublicUser | User | AnonymousUser | APITokenUser,
    organization: Organization,
    capability: StudytomyCapabilityName,
    *,
    required_responsibility: Optional[StudyOwnerFunction] = None,
    target_resource_uuid: Optional[str] = None,
    target_scope_kind: Optional[str] = None,
    subject_user_id: Optional[int] = None,
    excluded_user_ids: Optional[Collection[int]] = None,
    access_version: int = 1,
    hide_existence_on_denial: bool = False,
    now: Optional[datetime] = None,
) -> StudytomyAccessDecision:
    """Enforce `evaluate_studytomy_staff_access` and raise HTTP 401/403/404 on denial."""
    decision = await evaluate_studytomy_staff_access(
        db_session,
        current_user,
        organization,
        capability,
        required_responsibility=required_responsibility,
        target_resource_uuid=target_resource_uuid,
        target_scope_kind=target_scope_kind,
        subject_user_id=subject_user_id,
        excluded_user_ids=excluded_user_ids,
        access_version=access_version,
        now=now,
    )
    if decision.allowed:
        return decision

    if decision.code == "AUTHENTICATION_REQUIRED":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=decision.model_dump(),
        )

    if decision.code == "POLICY_NOT_APPROVED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=decision.model_dump(),
        )

    if hide_existence_on_denial and decision.code in {
        "ORG_SCOPE_DENIED",
        "RESOURCE_SCOPE_REQUIRED",
        "RESOURCE_SCOPE_UNASSIGNED",
        "CONFLICT_OF_INTEREST_EXCLUDED",
    }:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "RESOURCE_NOT_FOUND",
                "message": "Requested resource was not found.",
            },
        )

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=decision.model_dump(),
    )


def evaluate_learner_academic_read_access(
    *,
    actor_user_id: int,
    learner_user_id: int,
    is_payer_only: bool = False,
    has_active_guardian_academic_scope: bool = False,
    has_active_voluntary_share_scope: bool = False,
    has_assigned_educator_scope: bool = False,
    excluded_user_ids: Optional[Collection[int]] = None,
    policy_version_ids: Optional[list[int]] = None,
    access_version: int = 1,
) -> StudytomyAccessDecision:
    """Enforce Invariant 1 & TASK-003 acceptance: a payer cannot read learner marks
    unless they independently hold an active guardian academic scope, an active
    independent-learner voluntary share, or an assigned educator scope, and are not
    excluded by a restriction or conflict of interest.
    """
    resolved_policy_ids = list(policy_version_ids or [])

    if excluded_user_ids is not None and int(actor_user_id) in {
        int(uid) for uid in excluded_user_ids
    }:
        return StudytomyAccessDecision(
            allowed=False,
            code="RESTRICTED_OR_EXCLUDED",
            scope=None,
            policy_version_ids=resolved_policy_ids,
            access_version=access_version,
            next_action="Contact family services if you believe this restriction is in error",
            public_message="Academic records are not available for this account.",
        )

    if int(actor_user_id) == int(learner_user_id):
        return StudytomyAccessDecision(
            allowed=True,
            code="ALLOWED",
            scope="learner:self",
            policy_version_ids=resolved_policy_ids,
            access_version=access_version,
            next_action=None,
            public_message="Access granted.",
        )

    if (
        has_active_guardian_academic_scope
        or has_active_voluntary_share_scope
        or has_assigned_educator_scope
    ):
        scope_kind = (
            "guardian:academic"
            if has_active_guardian_academic_scope
            else (
                "learner:voluntary_share"
                if has_active_voluntary_share_scope
                else "staff:assigned_educator"
            )
        )
        return StudytomyAccessDecision(
            allowed=True,
            code="ALLOWED",
            scope=scope_kind,
            policy_version_ids=resolved_policy_ids,
            access_version=access_version,
            next_action=None,
            public_message="Access granted.",
        )

    if is_payer_only:
        return StudytomyAccessDecision(
            allowed=False,
            code="PAYER_ACADEMIC_ACCESS_DENIED",
            scope="commerce:payer_only",
            policy_version_ids=resolved_policy_ids,
            access_version=access_version,
            next_action="Use the purchase receipt view for payment status, or request verified guardian authority",
            public_message="Payment ownership does not grant access to a learner's marks or academic work.",
        )

    return StudytomyAccessDecision(
        allowed=False,
        code="ACADEMIC_ACCESS_DENIED",
        scope=None,
        policy_version_ids=resolved_policy_ids,
        access_version=access_version,
        next_action=None,
        public_message="You do not have permission to view this learner's academic records.",
    )


async def authorize_financial_delegation(
    db_session: AsyncSession,
    current_user: PublicUser | User | AnonymousUser | APITokenUser,
    organization: Organization,
    *,
    amount_minor: int,
    currency: str,
    reason: str,
    target_order_uuid: str,
    requester_user_id: int,
    beneficiary_user_id: Optional[int] = None,
    excluded_user_ids: Optional[Collection[int]] = None,
    now: Optional[datetime] = None,
) -> StudytomyAccessDecision:
    """Enforce reasoned financial delegation limits for refund/adjustment approvals (TASK-003, BR-PAY-08, D-19)."""
    if amount_minor <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_FINANCIAL_AMOUNT",
                "message": "Approved financial amount must be greater than zero minor units.",
            },
        )
    if not reason or not reason.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "REASON_REQUIRED",
                "message": "A documented reason is required for financial approval.",
            },
        )

    normalized_currency = currency.strip().upper()

    decision = await authorize_studytomy_staff_action(
        db_session,
        current_user,
        organization,
        StudytomyCapabilityName.REFUND_APPROVAL,
        required_responsibility=StudyOwnerFunction.COMMERCIAL,
        target_resource_uuid=target_order_uuid,
        target_scope_kind="order",
        subject_user_id=requester_user_id,
        excluded_user_ids=(
            set(excluded_user_ids or ())
            | ({beneficiary_user_id} if beneficiary_user_id is not None else set())
        ),
        now=now,
    )

    active_assignment = await _find_active_owner_assignment(
        db_session,
        int(organization.id),
        organization.org_uuid,
        int(current_user.id),
        StudyOwnerFunction.COMMERCIAL,
        now or datetime.now(timezone.utc),
    )
    policy_id = decision.policy_version_ids[0]
    if (
        active_assignment is None
        or active_assignment.financial_limit_minor is None
        or active_assignment.financial_limit_currency is None
        or not active_assignment.delegation_evidence_reference
        or active_assignment.delegation_policy_version_id != policy_id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "FINANCIAL_DELEGATION_REQUIRED",
                "message": "A documented financial delegation under the active D-19 policy is required.",
            },
        )

    stored_currency = active_assignment.financial_limit_currency.strip().upper()
    if normalized_currency != stored_currency:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "DELEGATION_CURRENCY_MISMATCH",
                "message": "The requested currency does not match the approved delegation currency.",
            },
        )

    if amount_minor > active_assignment.financial_limit_minor:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=StudytomyAccessDecision(
                allowed=False,
                code="DELEGATION_LIMIT_EXCEEDED",
                scope=decision.scope,
                policy_version_ids=[policy_id],
                access_version=decision.access_version,
                next_action="Escalate to an owner with a higher approved financial delegation limit",
                public_message="The requested amount exceeds your approved financial delegation limit.",
            ).model_dump(),
        )

    return decision
