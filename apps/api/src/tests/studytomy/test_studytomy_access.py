"""Focused negative authorization, scoped-privilege, and governance tests for TASK-002 and TASK-003.

Verifies against the live governance and security models without weakening any safety assertions:
1. Backward-compatible `Rights` serialization with default-deny `StudytomyCapabilities`.
2. Rejection of `AnonymousUser` (401) and `APITokenUser` (403) on Studytomy staff operations.
3. Organization Admin teaching rights (`ADMIN_RIGHTS`) do not grant unrestricted safeguarding,
   family-authority, learner-dossier, or financial approval capabilities.
4. Policy gate (`409 POLICY_NOT_APPROVED` when `D-03`/`D-19` is unapproved) + capability +
   active organization-scoped `StudyOwnerAssignment` + persisted `StudyStaffScope` resource/case scope.
5. Unassigned educator denial on learner dossier read (including 404 existence hiding and
   explicit `is_exclusion=True` scope override).
6. Cross-organization identifier substitution denial.
7. Conflict-of-interest and self-approval exclusion overriding Admin + active owner assignment.
8. Payer-only denial on learner marks/academic records.
9. Reasoned financial delegation limit enforcement (persisted `financial_limit_minor`,
   `financial_limit_currency`, `delegation_policy_version_id`, currency mismatch, ceiling,
   reason, and self-requester/beneficiary exclusion).
10. Governance policy lifecycle (`DRAFT` -> multi-owner `APPROVED` -> `SUPERSEDED`), overlap
    rejection (409), member-removal owner termination, and org/user deletion protection (409).
"""

from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.roles import Rights, Role, RoleTypeEnum, StudytomyCapabilities
from src.db.studytomy.commerce import StudyOffer, StudyOfferVersion
from src.db.studytomy.governance import (
    POLICY_REQUIRED_DECISION_AREAS,
    StudyOwnerAssignment,
    StudyOwnerAssignmentCreate,
    StudyOwnerFunction,
    StudyOwnerStatus,
    StudyPolicyApproval,
    StudyPolicyApprovalRequest,
    StudyPolicyBody,
    StudyPolicyDecisionState,
    StudyPolicyDecisionValue,
    StudyPolicyKey,
    StudyPolicyProposal,
    StudyPolicyStatus,
    StudyPolicyVersion,
    StudyStaffScope,
)
from src.db.user_organizations import UserOrganization
from src.db.users import AnonymousUser, APITokenUser, User
from src.routers.studytomy.governance import router as governance_router
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.security.auth import get_current_user
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_financial_delegation,
    authorize_studytomy_staff_action,
    evaluate_learner_academic_read_access,
    evaluate_studytomy_staff_access,
)
from src.services.orgs.orgs import delete_org
from src.services.orgs.users import remove_user_from_org
from src.services.studytomy.policies import (
    approve_policy_version,
    create_owner_assignment,
    propose_policy_version,
)
from src.services.users.users import delete_user_by_id
from src.tests.conftest import ADMIN_RIGHTS, USER_RIGHTS


def _synthetic_policy_body(
    policy_key: StudyPolicyKey,
    specified: dict[str, object] | None = None,
) -> StudyPolicyBody:
    """Complete test-only policy body; it contains no approved business choices."""
    specified = specified or {}
    items = {
        area: StudyPolicyDecisionValue(
            state=StudyPolicyDecisionState.NOT_APPLICABLE,
            rationale="Synthetic test fixture only.",
            evidence_reference=f"TEST-{policy_key.value}-{area}",
        )
        for area in POLICY_REQUIRED_DECISION_AREAS[policy_key]
    }
    for area, value in specified.items():
        items[area] = StudyPolicyDecisionValue(
            state=StudyPolicyDecisionState.SPECIFIED,
            value=value,
            rationale="Synthetic test fixture only.",
            evidence_reference=f"TEST-{policy_key.value}-{area}",
        )
    return StudyPolicyBody(decision_items=items)


async def _grant_role_capabilities(
    db, org_id: int, user_id: int, **capability_flags: bool
) -> Role:
    """Create and bind an organization role with specific Studytomy capabilities enabled."""
    rights_dict = ADMIN_RIGHTS.model_dump()
    rights_dict["studytomy"] = StudytomyCapabilities(**capability_flags).model_dump()
    role = Role(
        name=f"ScopedRole_{user_id}",
        description="Test scoped role",
        id=None,
        org_id=org_id,
        role_type=RoleTypeEnum.TYPE_ORGANIZATION,
        role_uuid=f"role_scoped_{org_id}_{user_id}",
        rights=rights_dict,
        creation_date=str(datetime.now(timezone.utc)),
        update_date=str(datetime.now(timezone.utc)),
    )
    db.add(role)
    await db.commit()
    await db.refresh(role)

    uo = (
        await db.execute(
            select(UserOrganization).where(
                UserOrganization.user_id == user_id,
                UserOrganization.org_id == org_id,
            )
        )
    ).scalars().first()
    if uo is None:
        uo = UserOrganization(
            user_id=user_id,
            org_id=org_id,
            role_id=role.id,
            creation_date=str(datetime.now(timezone.utc)),
            update_date=str(datetime.now(timezone.utc)),
        )
    else:
        uo.role_id = role.id
    db.add(uo)
    await db.commit()
    return role


async def _seed_approved_policy(
    db,
    org: Organization,
    creator_id: int,
    policy_key: StudyPolicyKey,
    *,
    specified: dict[str, object] | None = None,
    version: int = 1,
) -> StudyPolicyVersion:
    now = datetime.now(timezone.utc)
    policy = StudyPolicyVersion(
        org_id=org.id,
        policy_key=policy_key.value,
        scope_kind="organization",
        scope_uuid=org.org_uuid,
        version=version,
        status=StudyPolicyStatus.APPROVED.value,
        body=_synthetic_policy_body(
            policy_key,
            specified if specified is not None else {
                "staff_authority": {"fixture": True},
                "refund_limits": {"max_amount_minor": 15_000, "currency": "AUD"},
                "exceptions": {"fixture": True},
                "financial_review_frequency": {"fixture": "monthly"},
            }
            if policy_key == StudyPolicyKey.D_19
            else None,
        ).model_dump(mode="json"),
        evidence_reference=f"EV-{policy_key.value}-APPROVED",
        effective_from=now - timedelta(days=1),
        effective_to=now + timedelta(days=30),
        created_by_user_id=creator_id,
        approved_by_user_id=creator_id,
        approved_at=now,
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return policy


async def _seed_active_owner(
    db,
    org: Organization,
    user_id: int,
    assigned_by_id: int,
    responsibility: StudyOwnerFunction,
    *,
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
    status_val: StudyOwnerStatus = StudyOwnerStatus.ACTIVE,
    financial_limit_minor: int | None = None,
    financial_limit_currency: str | None = None,
    delegation_evidence_reference: str | None = None,
    delegation_policy_version_id: int | None = None,
) -> StudyOwnerAssignment:
    now = datetime.now(timezone.utc)
    assignment = StudyOwnerAssignment(
        org_id=org.id,
        user_id=user_id,
        responsibility=responsibility.value,
        scope_kind="organization",
        scope_uuid=org.org_uuid,
        status=status_val.value,
        evidence_reference="EV-OWNER-001",
        effective_from=effective_from or (now - timedelta(days=1)),
        effective_to=effective_to,
        financial_limit_minor=financial_limit_minor,
        financial_limit_currency=financial_limit_currency,
        delegation_evidence_reference=delegation_evidence_reference,
        delegation_policy_version_id=delegation_policy_version_id,
        assigned_by_user_id=assigned_by_id,
    )
    db.add(assignment)
    await db.commit()
    await db.refresh(assignment)
    return assignment


async def _seed_staff_scope(
    db,
    org_id: int,
    assignment_id: int,
    capability: StudytomyCapabilityName,
    scope_kind: str,
    scope_uuid: str,
    created_by_id: int,
    *,
    is_exclusion: bool = False,
) -> StudyStaffScope:
    scope = StudyStaffScope(
        org_id=org_id,
        assignment_id=assignment_id,
        capability=capability.value,
        scope_kind=scope_kind,
        scope_uuid=scope_uuid,
        is_exclusion=is_exclusion,
        status="active",
        evidence_reference="EV-SCOPE-001",
        created_by_user_id=created_by_id,
    )
    db.add(scope)
    await db.commit()
    await db.refresh(scope)
    return scope


async def _verify_content_review_access(db, org, reviewer, assigner):
    await _grant_role_capabilities(db, org.id, reviewer.id, content_release_review=True)

    async def evaluate(**scope):
        return await evaluate_studytomy_staff_access(
            db, reviewer, org, StudytomyCapabilityName.CONTENT_RELEASE_REVIEW,
            target_scope_kind="course", target_resource_uuid="course_review_fixture", **scope,
        )

    ids = []
    for key in (StudyPolicyKey.D_02, StudyPolicyKey.D_10, StudyPolicyKey.D_15, StudyPolicyKey.D_16):
        denied = await evaluate()
        assert not denied.allowed
        assert denied.code == "POLICY_NOT_APPROVED"
        assert denied.policy_version_ids == []
        ids.append((await _seed_approved_policy(db, org, assigner.id, key)).id)

    unassigned = await evaluate()
    assert not unassigned.allowed
    assert unassigned.code == "OWNER_ASSIGNMENT_REQUIRED"
    assert unassigned.policy_version_ids == ids

    owner = await _seed_active_owner(db, org, reviewer.id, assigner.id, StudyOwnerFunction.EDUCATION)
    assert (await evaluate()).code == "RESOURCE_SCOPE_UNASSIGNED"
    await _seed_staff_scope(
        db, org.id, owner.id, StudytomyCapabilityName.CONTENT_RELEASE_REVIEW,
        "course", "course_review_fixture", assigner.id,
    )
    assert (await evaluate()).allowed
    self_review = await evaluate(excluded_user_ids={reviewer.id})
    assert not self_review.allowed
    assert self_review.code == "CONFLICT_OF_INTEREST_EXCLUDED"


@pytest.mark.studytomy_postgres
async def test_content_reviewer_gate_on_postgres(postgres_test_rows):
    rows = postgres_test_rows
    async with rows.session_factory() as outer:
        connection = await outer.connection()
        async with AsyncSession(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False) as db:
            org = await db.get(Organization, rows.org_id)
            reviewer = await db.get(User, rows.user_id)
            stamp = datetime.now(UTC).isoformat()
            assigner = User(
                username=f"review_owner_{rows.token}", user_uuid=f"user_{rows.token}",
                email=f"review-owner-{rows.token}@example.com",
                first_name="Synthetic", last_name="Owner", creation_date=stamp, update_date=stamp,
            )
            db.add(assigner)
            await db.commit()
            await _verify_content_review_access(db, org, reviewer, assigner)
        await outer.rollback()


class TestRoleSerializationAndDefaultDeny:
    """Verifies TASK-003 backward compatibility and default-deny capability model."""

    def test_legacy_rights_payload_deserializes_with_default_deny_studytomy(self):
        legacy_payload = USER_RIGHTS.model_dump()
        legacy_payload.pop("studytomy", None)

        parsed = Rights.model_validate(legacy_payload)
        assert isinstance(parsed.studytomy, StudytomyCapabilities)
        assert parsed.studytomy.family_authority_review is False
        assert parsed.studytomy.learner_dossier_read is False
        assert parsed.studytomy.safeguarding_case_review is False
        assert parsed.studytomy.financial_reconciliation is False
        assert parsed.studytomy.refund_approval is False

    def test_admin_rights_preserve_teaching_without_unrestricted_studytomy_authority(
        self,
    ):
        assert ADMIN_RIGHTS.courses.action_create is True
        assert ADMIN_RIGHTS.activities.action_update is True
        assert ADMIN_RIGHTS.studytomy.safeguarding_case_review is False
        assert ADMIN_RIGHTS.studytomy.family_authority_review is False
        assert ADMIN_RIGHTS.studytomy.refund_approval is False


class TestStudytomyStaffBoundaryAndExclusions:
    """Verifies negative authorization, scope enforcement, and conflict exclusions (TASK-003)."""

    async def test_anonymous_and_api_token_principals_are_rejected(self, db, org):
        with pytest.raises(HTTPException) as anon_exc:
            await authorize_studytomy_staff_action(
                db,
                AnonymousUser(),
                org,
                StudytomyCapabilityName.LEARNER_DOSSIER_READ,
            )
        assert anon_exc.value.status_code == 401
        assert anon_exc.value.detail["code"] == "AUTHENTICATION_REQUIRED"

        token_user = APITokenUser(
            org_id=org.id,
            rights=ADMIN_RIGHTS.model_dump(),
            token_uuid="apitoken_test_1",
            created_by_user_id=1,
        )
        with pytest.raises(HTTPException) as token_exc:
            await authorize_studytomy_staff_action(
                db,
                token_user,
                org,
                StudytomyCapabilityName.LEARNER_DOSSIER_READ,
            )
        assert token_exc.value.status_code == 403
        assert token_exc.value.detail["code"] == "API_TOKEN_FORBIDDEN"

    async def test_admin_without_explicit_studytomy_capability_is_denied(
        self, db, org, admin_user
    ):
        # Even if marked superadmin, generic Admin role has default-deny Studytomy capabilities
        admin_user.is_superadmin = True
        for cap in (
            StudytomyCapabilityName.SAFEGUARDING_CASE_REVIEW,
            StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW,
            StudytomyCapabilityName.LEARNER_DOSSIER_READ,
            StudytomyCapabilityName.REFUND_APPROVAL,
        ):
            decision = await evaluate_studytomy_staff_access(
                db, admin_user, org, cap, target_resource_uuid="res_1"
            )
            assert decision.allowed is False
            assert decision.code == "CAPABILITY_DENIED"

    async def test_unassigned_educator_cannot_read_learner_dossier(
        self, db, org, admin_user, regular_user
    ):
        await _grant_role_capabilities(
            db, org.id, admin_user.id, learner_dossier_read=True
        )

        # Without D-03 policy approved -> 409 POLICY_NOT_APPROVED
        with pytest.raises(HTTPException) as unapproved_exc:
            await authorize_studytomy_staff_action(
                db,
                admin_user,
                org,
                StudytomyCapabilityName.LEARNER_DOSSIER_READ,
                target_resource_uuid="learner_assigned_01",
            )
        assert unapproved_exc.value.status_code == 409
        assert unapproved_exc.value.detail["code"] == "POLICY_NOT_APPROVED"

        # Seed approved D-03 policy and active owner assignment + scope for learner_assigned_01
        d03_policy = await _seed_approved_policy(
            db, org, regular_user.id, StudyPolicyKey.D_03
        )
        owner = await _seed_active_owner(
            db, org, admin_user.id, regular_user.id, StudyOwnerFunction.EDUCATION
        )
        await _seed_staff_scope(
            db,
            org.id,
            owner.id,
            StudytomyCapabilityName.LEARNER_DOSSIER_READ,
            "learner",
            "learner_assigned_01",
            regular_user.id,
        )

        # Unassigned learner -> 403 (or 404 when hide_existence_on_denial=True)
        with pytest.raises(HTTPException) as unassigned_exc:
            await authorize_studytomy_staff_action(
                db,
                admin_user,
                org,
                StudytomyCapabilityName.LEARNER_DOSSIER_READ,
                target_resource_uuid="learner_unassigned_99",
            )
        assert unassigned_exc.value.status_code == 403
        assert unassigned_exc.value.detail["code"] == "RESOURCE_SCOPE_UNASSIGNED"

        with pytest.raises(HTTPException) as hidden_exc:
            await authorize_studytomy_staff_action(
                db,
                admin_user,
                org,
                StudytomyCapabilityName.LEARNER_DOSSIER_READ,
                target_resource_uuid="learner_unassigned_99",
                hide_existence_on_denial=True,
            )
        assert hidden_exc.value.status_code == 404

        # Assigned learner -> allowed
        allowed = await authorize_studytomy_staff_action(
            db,
            admin_user,
            org,
            StudytomyCapabilityName.LEARNER_DOSSIER_READ,
            target_resource_uuid="learner_assigned_01",
        )
        assert allowed.allowed is True
        assert allowed.scope == f"org:{org.org_uuid}:resource:learner_assigned_01"
        assert allowed.policy_version_ids == [d03_policy.id]

        # A legacy or malformed body cannot satisfy an approved-policy gate.
        d03_policy.body = {"values": {"test_fixture": True}}
        await db.commit()
        with pytest.raises(HTTPException) as invalid_policy_exc:
            await authorize_studytomy_staff_action(
                db,
                admin_user,
                org,
                StudytomyCapabilityName.LEARNER_DOSSIER_READ,
                target_resource_uuid="learner_assigned_01",
            )
        assert invalid_policy_exc.value.status_code == 403
        assert invalid_policy_exc.value.detail["code"] == "POLICY_BODY_INVALID"

    async def test_cross_org_identifier_substitution_is_denied(
        self, db, org, admin_user
    ):
        await _grant_role_capabilities(
            db, org.id, admin_user.id, family_authority_review=True
        )
        await _seed_active_owner(
            db, org, admin_user.id, admin_user.id, StudyOwnerFunction.FAMILY_SERVICES
        )

        other_org = Organization(
            id=999,
            name="Other Org",
            slug="other-org",
            email="other@org.test",
            org_uuid="org_other_999",
            creation_date="2026-11-15T00:00:00Z",
            update_date="2026-11-15T00:00:00Z",
        )
        db.add(other_org)
        await db.commit()

        with pytest.raises(HTTPException) as cross_exc:
            await authorize_studytomy_staff_action(
                db,
                admin_user,
                other_org,
                StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW,
                required_responsibility=StudyOwnerFunction.FAMILY_SERVICES,
                target_resource_uuid="learner_01",
            )
        assert cross_exc.value.status_code == 403
        assert cross_exc.value.detail["code"] == "ORG_SCOPE_DENIED"

    async def test_family_authority_review_requires_both_d03_and_d12(
        self, db, org, admin_user, regular_user
    ):
        await _grant_role_capabilities(
            db, org.id, admin_user.id, family_authority_review=True
        )
        d03 = await _seed_approved_policy(
            db, org, regular_user.id, StudyPolicyKey.D_03
        )

        missing_d12 = await evaluate_studytomy_staff_access(
            db,
            admin_user,
            org,
            StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW,
            target_resource_uuid="learner_authority_01",
        )
        assert missing_d12.allowed is False
        assert missing_d12.code == "POLICY_NOT_APPROVED"
        assert missing_d12.policy_version_ids == []

        d12 = await _seed_approved_policy(
            db, org, regular_user.id, StudyPolicyKey.D_12
        )
        unassigned = await evaluate_studytomy_staff_access(
            db,
            admin_user,
            org,
            StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW,
            target_resource_uuid="learner_authority_01",
        )
        assert unassigned.allowed is False
        assert unassigned.code == "OWNER_ASSIGNMENT_REQUIRED"
        assert unassigned.policy_version_ids == [d03.id, d12.id]

    async def test_safeguarding_case_review_requires_all_case_policy_gates(
        self, db, org, admin_user, regular_user
    ):
        await _grant_role_capabilities(
            db, org.id, admin_user.id, safeguarding_case_review=True
        )
        required = [
            StudyPolicyKey.D_04,
            StudyPolicyKey.D_11,
            StudyPolicyKey.D_12,
            StudyPolicyKey.D_13,
        ]

        blocked = await evaluate_studytomy_staff_access(
            db,
            admin_user,
            org,
            StudytomyCapabilityName.SAFEGUARDING_CASE_REVIEW,
            target_resource_uuid="case_safeguarding_02",
        )
        assert blocked.allowed is False
        assert blocked.code == "POLICY_NOT_APPROVED"
        assert blocked.policy_version_ids == []

        policy_ids = []
        for policy_key in required:
            policy = await _seed_approved_policy(db, org, regular_user.id, policy_key)
            policy_ids.append(policy.id)

        unassigned = await evaluate_studytomy_staff_access(
            db,
            admin_user,
            org,
            StudytomyCapabilityName.SAFEGUARDING_CASE_REVIEW,
            target_resource_uuid="case_safeguarding_02",
        )
        assert unassigned.allowed is False
        assert unassigned.code == "OWNER_ASSIGNMENT_REQUIRED"
        assert unassigned.policy_version_ids == policy_ids

    async def test_content_reviewer_requires_all_release_policies_and_independent_authority(
        self, db, org, admin_user, regular_user
    ):
        await _verify_content_review_access(db, org, admin_user, regular_user)

    async def test_subject_of_complaint_or_self_approval_is_excluded_even_for_privileged_admin(
        self, db, org, admin_user, regular_user
    ):
        await _grant_role_capabilities(
            db,
            org.id,
            admin_user.id,
            safeguarding_case_review=True,
            family_authority_review=True,
        )
        await _seed_active_owner(
            db, org, admin_user.id, regular_user.id, StudyOwnerFunction.SAFEGUARDING
        )
        await _seed_active_owner(
            db, org, admin_user.id, regular_user.id, StudyOwnerFunction.FAMILY_SERVICES
        )
        await _seed_approved_policy(
            db, org, regular_user.id, StudyPolicyKey.D_03
        )
        await _seed_approved_policy(
            db, org, regular_user.id, StudyPolicyKey.D_12
        )

        # 1. Admin is named as an excluded party in a safeguarding complaint
        with pytest.raises(HTTPException) as complaint_exc:
            await authorize_studytomy_staff_action(
                db,
                admin_user,
                org,
                StudytomyCapabilityName.SAFEGUARDING_CASE_REVIEW,
                required_responsibility=StudyOwnerFunction.SAFEGUARDING,
                target_resource_uuid="case_safeguarding_01",
                excluded_user_ids={admin_user.id},
            )
        assert complaint_exc.value.status_code == 403
        assert complaint_exc.value.detail["code"] == "CONFLICT_OF_INTEREST_EXCLUDED"

        # 2. Admin attempts self-approval of a family relationship request
        with pytest.raises(HTTPException) as self_exc:
            await authorize_studytomy_staff_action(
                db,
                admin_user,
                org,
                StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW,
                required_responsibility=StudyOwnerFunction.FAMILY_SERVICES,
                target_resource_uuid="learner_01",
                subject_user_id=admin_user.id,
            )
        assert self_exc.value.status_code == 403
        assert self_exc.value.detail["code"] == "CONFLICT_OF_INTEREST_EXCLUDED"

    def test_payer_cannot_read_learner_marks_without_academic_authority(self):
        # Payer-only is denied academic marks
        payer_decision = evaluate_learner_academic_read_access(
            actor_user_id=20,
            learner_user_id=30,
            is_payer_only=True,
        )
        assert payer_decision.allowed is False
        assert payer_decision.code == "PAYER_ACADEMIC_ACCESS_DENIED"

        # Excluded guardian is denied even if flag is set
        excluded_decision = evaluate_learner_academic_read_access(
            actor_user_id=20,
            learner_user_id=30,
            has_active_guardian_academic_scope=True,
            excluded_user_ids={20},
        )
        assert excluded_decision.allowed is False
        assert excluded_decision.code == "RESTRICTED_OR_EXCLUDED"

        # Verified active guardian academic scope is allowed
        guardian_decision = evaluate_learner_academic_read_access(
            actor_user_id=20,
            learner_user_id=30,
            has_active_guardian_academic_scope=True,
            policy_version_ids=[1],
        )
        assert guardian_decision.allowed is True
        assert guardian_decision.scope == "guardian:academic"

    async def test_financial_delegation_limits_and_self_refund_exclusion(
        self, db, org, admin_user, regular_user
    ):
        await _grant_role_capabilities(
            db, org.id, admin_user.id, refund_approval=True
        )
        d19_policy = await _seed_approved_policy(
            db, org, regular_user.id, StudyPolicyKey.D_19
        )
        owner = await _seed_active_owner(
            db,
            org,
            admin_user.id,
            regular_user.id,
            StudyOwnerFunction.COMMERCIAL,
            financial_limit_minor=15000,
            financial_limit_currency="AUD",
            delegation_evidence_reference="DEL-FIN-2026-01",
            delegation_policy_version_id=d19_policy.id,
        )
        await _seed_staff_scope(
            db,
            org.id,
            owner.id,
            StudytomyCapabilityName.REFUND_APPROVAL,
            "order",
            "order_refund_01",
            regular_user.id,
        )

        # Currency mismatch -> 409
        with pytest.raises(HTTPException) as curr_exc:
            await authorize_financial_delegation(
                db,
                admin_user,
                org,
                amount_minor=5000,
                currency="USD",
                reason="Approved cancellation refund",
                target_order_uuid="order_refund_01",
                requester_user_id=regular_user.id,
                beneficiary_user_id=regular_user.id,
            )
        assert curr_exc.value.status_code == 409
        assert curr_exc.value.detail["code"] == "DELEGATION_CURRENCY_MISMATCH"

        # Above limit -> 403
        with pytest.raises(HTTPException) as ceiling_exc:
            await authorize_financial_delegation(
                db,
                admin_user,
                org,
                amount_minor=20000,
                currency="AUD",
                reason="Exceeds limit",
                target_order_uuid="order_refund_01",
                requester_user_id=regular_user.id,
                beneficiary_user_id=regular_user.id,
            )
        assert ceiling_exc.value.status_code == 403
        assert ceiling_exc.value.detail["code"] == "DELEGATION_LIMIT_EXCEEDED"

        # Self-refund approval -> 403 CONFLICT_OF_INTEREST_EXCLUDED
        with pytest.raises(HTTPException) as self_ref_exc:
            await authorize_financial_delegation(
                db,
                admin_user,
                org,
                amount_minor=5000,
                currency="AUD",
                reason="Self refund",
                target_order_uuid="order_refund_01",
                requester_user_id=admin_user.id,
                beneficiary_user_id=admin_user.id,
            )
        assert self_ref_exc.value.status_code == 403
        assert self_ref_exc.value.detail["code"] == "CONFLICT_OF_INTEREST_EXCLUDED"

        # Valid delegated refund approval -> allowed
        approved = await authorize_financial_delegation(
            db,
            admin_user,
            org,
            amount_minor=15000,
            currency="AUD",
            reason="Verified duplicate payment return",
            target_order_uuid="order_refund_01",
            requester_user_id=regular_user.id,
            beneficiary_user_id=regular_user.id,
        )
        assert approved.allowed is True
        assert approved.policy_version_ids == [d19_policy.id]


async def test_draft_offer_history_blocks_cleanup(db, org, admin_user, regular_user, mock_request):
    offer = StudyOffer(
        org_id=org.id,
        offer_uuid=f"offer_{uuid4()}",
        offer_kind="course",
        created_by_user_id=admin_user.id,
    )
    db.add(offer)
    await db.flush()
    version = StudyOfferVersion(
        offer_id=offer.id,
        version_no=1,
        terms={},
        content_sha256="0" * 64,
        created_by_user_id=regular_user.id,
    )
    db.add(version)
    await db.commit()
    with (
        patch("src.services.users.users.rbac_check", new_callable=AsyncMock),
        pytest.raises(HTTPException) as user_error,
    ):
        await delete_user_by_id(mock_request, db, regular_user, regular_user.id)
    assert user_error.value.status_code == 409
    with (
        patch("src.services.orgs.orgs.rbac_check", new_callable=AsyncMock),
        patch("src.services.orgs.orgs.require_org_destroy_right", new_callable=AsyncMock),
        pytest.raises(HTTPException) as org_error,
    ):
        await delete_org(mock_request, org.id, admin_user, db)
    assert org_error.value.status_code == 409
    await db.refresh(offer)
    await db.refresh(version)
    assert await db.get(User, regular_user.id) is not None
    assert await db.get(Organization, org.id) is not None


class TestGovernanceLifecycleAndDestructiveGuards:
    """Verifies TASK-002 policy/owner lifecycle, overlap rejection, and deletion guards."""

    def test_policy_decision_values_require_explicit_evidence(self):
        with pytest.raises(ValidationError):
            StudyPolicyDecisionValue(
                state=StudyPolicyDecisionState.SPECIFIED,
                value="course_only",
                rationale="A rationale without a source record",
            )
        with pytest.raises(ValidationError):
            StudyPolicyDecisionValue(
                state=StudyPolicyDecisionState.PENDING,
                value="course_only",
            )
        with pytest.raises(ValidationError):
            StudyPolicyDecisionValue(
                state=StudyPolicyDecisionState.SPECIFIED,
                value={},
                rationale="Empty values cannot resolve a decision",
                evidence_reference="TEST-EMPTY",
            )
        with pytest.raises(ValidationError):
            StudyPolicyDecisionValue(
                state=StudyPolicyDecisionState.SPECIFIED,
                value={"supported_formats": []},
                rationale="Empty nested collections cannot resolve a decision",
                evidence_reference="TEST-EMPTY-LIST",
            )

    def test_proposal_rejects_decision_areas_from_another_policy(self):
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            StudyPolicyProposal(
                policy_key=StudyPolicyKey.D_01,
                body=StudyPolicyBody(
                    decision_items={
                        "curriculum_claims": StudyPolicyDecisionValue(
                            state=StudyPolicyDecisionState.NOT_APPLICABLE,
                            rationale="Synthetic test fixture only.",
                            evidence_reference="TEST-WRONG-AREA",
                        )
                    }
                ),
                evidence_reference="EV-D01-TEST-INVALID",
                effective_from=now,
            )

    async def test_incomplete_policy_cannot_receive_any_approval(
        self, db, org, admin_user
    ):
        now = datetime.now(UTC)
        draft = await propose_policy_version(
            db,
            org,
            admin_user.id,
            StudyPolicyProposal(
                policy_key=StudyPolicyKey.D_01,
                body=StudyPolicyBody(),
                evidence_reference="EV-D01-TEST-INCOMPLETE",
                effective_from=now,
            ),
        )

        with pytest.raises(HTTPException) as exc:
            await approve_policy_version(
                db,
                org,
                admin_user.id,
                draft.policy_uuid,
                StudyPolicyApprovalRequest(
                    responsibility=StudyOwnerFunction.BUSINESS,
                    evidence_reference="EV-D01-TEST-APPROVAL",
                    rationale="Approval must remain blocked while decisions are missing.",
                ),
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "POLICY_BODY_INCOMPLETE"
        assert set(exc.value.detail["blockers"]["missing_decision_areas"]) == set(
            POLICY_REQUIRED_DECISION_AREAS[StudyPolicyKey.D_01]
        )

        stored = (
            await db.execute(
                select(StudyPolicyVersion).where(
                    StudyPolicyVersion.policy_uuid == draft.policy_uuid
                )
            )
        ).scalars().one()
        approvals = (
            await db.execute(
                select(StudyPolicyApproval.id).where(
                    StudyPolicyApproval.policy_version_id == stored.id
                )
            )
        ).scalars().all()
        assert stored.status == StudyPolicyStatus.DRAFT.value
        assert approvals == []

    async def test_policy_lifecycle_requires_all_named_owners_and_rejects_overlap(
        self, db, org, admin_user, regular_user
    ):
        now = datetime.now(timezone.utc)

        # Propose D-01 (requires both BUSINESS and SAFEGUARDING owner approvals)
        proposal = StudyPolicyProposal(
            policy_key=StudyPolicyKey.D_01,
            body=StudyPolicyBody(
                decision_items=_synthetic_policy_body(StudyPolicyKey.D_01).decision_items,
            ),
            evidence_reference="EV-D01-TEST-001",
            effective_from=now,
            effective_to=now + timedelta(days=30),
        )
        draft = await propose_policy_version(db, org, admin_user.id, proposal)
        assert draft.status == StudyPolicyStatus.DRAFT
        assert draft.version == 1

        # Approving without an active owner assignment -> 403
        with pytest.raises(HTTPException) as no_owner_exc:
            await approve_policy_version(
                db,
                org,
                admin_user.id,
                draft.policy_uuid,
                StudyPolicyApprovalRequest(
                    responsibility=StudyOwnerFunction.BUSINESS,
                    evidence_reference="EV-APP-1",
                    rationale="Attempt without owner assignment",
                ),
            )
        assert no_owner_exc.value.status_code == 403

        # Assign admin_user as BUSINESS owner and regular_user as SAFEGUARDING owner
        await create_owner_assignment(
            db,
            org,
            admin_user.id,
            StudyOwnerAssignmentCreate(
                user_uuid=admin_user.user_uuid,
                responsibility=StudyOwnerFunction.BUSINESS,
                evidence_reference="EV-OWN-BUS",
                effective_from=now - timedelta(hours=1),
            ),
        )
        await create_owner_assignment(
            db,
            org,
            admin_user.id,
            StudyOwnerAssignmentCreate(
                user_uuid=regular_user.user_uuid,
                responsibility=StudyOwnerFunction.SAFEGUARDING,
                evidence_reference="EV-OWN-SAFE",
                effective_from=now - timedelta(hours=1),
            ),
        )

        # First approval (BUSINESS) leaves D-01 in DRAFT because SAFEGUARDING is still required
        after_first = await approve_policy_version(
            db,
            org,
            admin_user.id,
            draft.policy_uuid,
            StudyPolicyApprovalRequest(
                responsibility=StudyOwnerFunction.BUSINESS,
                evidence_reference="EV-APP-BUS",
                rationale="Business owner sign-off",
            ),
        )
        assert after_first.status == StudyPolicyStatus.DRAFT
        assert len(after_first.approvals) == 1

        # Second approval (SAFEGUARDING) transitions D-01 to APPROVED
        after_second = await approve_policy_version(
            db,
            org,
            regular_user.id,
            draft.policy_uuid,
            StudyPolicyApprovalRequest(
                responsibility=StudyOwnerFunction.SAFEGUARDING,
                evidence_reference="EV-APP-SAFE",
                rationale="Safeguarding owner sign-off",
            ),
        )
        assert after_second.status == StudyPolicyStatus.APPROVED
        assert len(after_second.approvals) == 2

        # Proposing and approving an overlapping D-01 version without superseding -> 409
        overlapping_draft = await propose_policy_version(
            db,
            org,
            admin_user.id,
            StudyPolicyProposal(
                policy_key=StudyPolicyKey.D_01,
                body=StudyPolicyBody(
                    decision_items=_synthetic_policy_body(StudyPolicyKey.D_01).decision_items,
                ),
                evidence_reference="EV-D01-OVERLAP",
                effective_from=now + timedelta(days=5),
                effective_to=now + timedelta(days=40),
            ),
        )
        await approve_policy_version(
            db,
            org,
            admin_user.id,
            overlapping_draft.policy_uuid,
            StudyPolicyApprovalRequest(
                responsibility=StudyOwnerFunction.BUSINESS,
                evidence_reference="EV-APP-BUS-2",
                rationale="First approval on overlap",
            ),
        )
        with pytest.raises(HTTPException) as overlap_exc:
            await approve_policy_version(
                db,
                org,
                regular_user.id,
                overlapping_draft.policy_uuid,
                StudyPolicyApprovalRequest(
                    responsibility=StudyOwnerFunction.SAFEGUARDING,
                    evidence_reference="EV-APP-SAFE-2",
                    rationale="Second approval triggers overlap check",
                ),
            )
        assert overlap_exc.value.status_code == 409

    async def test_governance_router_denies_non_admin_and_member_removal_ends_owner_assignment(
        self, mock_request, db, org, admin_user, regular_user
    ):
        now = datetime.now(timezone.utc)
        owner_read = await create_owner_assignment(
            db,
            org,
            admin_user.id,
            StudyOwnerAssignmentCreate(
                user_uuid=regular_user.user_uuid,
                responsibility=StudyOwnerFunction.SUPPORT,
                evidence_reference="EV-SUPPORT-OWNER",
                effective_from=now - timedelta(hours=1),
            ),
        )
        assert owner_read.status == StudyOwnerStatus.ACTIVE

        # Non-admin regular_user cannot list or create service owners via governance router
        app = FastAPI()
        app.include_router(governance_router, prefix="/api/v1/studytomy")
        app.dependency_overrides[get_db_session] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: regular_user
        app.dependency_overrides[get_authenticated_non_api_token_user] = (
            lambda: regular_user
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/v1/studytomy/orgs/{org.org_uuid}/service-owners"
            )
        assert resp.status_code == 403

        # Deleting an organization or user with governance history is blocked with 409
        with (
            patch("src.services.orgs.orgs.rbac_check", new_callable=AsyncMock),
            patch(
                "src.services.orgs.orgs.require_org_destroy_right",
                new_callable=AsyncMock,
            ),
        ):
            with pytest.raises(HTTPException) as del_org_exc:
                await delete_org(mock_request, org.id, admin_user, db)
        assert del_org_exc.value.status_code == 409

        with patch(
            "src.services.users.users.authorization_verify_if_user_is_anon",
            new_callable=AsyncMock,
        ):
            with pytest.raises(HTTPException) as del_user_exc:
                await delete_user_by_id(
                    mock_request, db, regular_user, regular_user.id
                )
        assert del_user_exc.value.status_code == 409

        # Removing regular_user from the organization ends their active StudyOwnerAssignment
        with patch("src.services.orgs.users.rbac_check", new_callable=AsyncMock):
            await remove_user_from_org(
                mock_request, org.id, regular_user.id, db, admin_user
            )

        ended_row = (
            await db.execute(
                select(StudyOwnerAssignment).where(
                    StudyOwnerAssignment.owner_uuid == owner_read.owner_uuid
                )
            )
        ).scalars().first()
        assert ended_row is not None
        assert ended_row.status == StudyOwnerStatus.ENDED.value
