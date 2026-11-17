from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.events.database import get_db_session
from src.db.organization_config import OrganizationConfig
from src.db.organizations import Organization
from src.db.studytomy.family import StudyEligibilityDecision, StudyLearnerProfile
from src.db.studytomy.governance import (
    StudyOwnerFunction,
    StudyPolicyApproval,
    StudyPolicyKey,
)
from src.db.users import AnonymousUser, APITokenUser, PublicUser, User
from src.router import v1_router
from src.routers.studytomy import adaptive
from src.security import auth
from src.security.session_context import SessionProvenance
from src.services.studytomy.adaptive_access import get_adaptive_availability
from src.services.studytomy.policies import POLICY_REQUIRED_RESPONSIBILITIES
from src.tests.studytomy.test_studytomy_access import (
    _seed_active_owner,
    _seed_approved_policy,
)


async def _set_toggle(db, org, value):
    db.add(
        OrganizationConfig(
            org_id=org.id,
            config={"admin_toggles": {"studytomy_adaptive_enabled": value}},
        )
    )
    await db.commit()


@pytest.mark.parametrize("value", [False, "true", 1, None])
async def test_malformed_or_false_toggle_disables_every_action(
    db, org, regular_user, value
):
    await _set_toggle(db, org, value)
    decision = await get_adaptive_availability(db, org, regular_user)
    assert decision.enabled is False
    assert decision.learner_eligible is False
    assert decision.allowed_actions == []
    assert set(decision.denials.values()) == {"ADAPTIVE_DISABLED"}


@pytest.mark.parametrize("expired_ai", [False, True])
async def test_approved_organization_policies_do_not_grant_learner_or_recipient_access(
    db,
    org,
    admin_user,
    regular_user,
    expired_ai,
):
    await _set_toggle(db, org, True)
    keys = (
        StudyPolicyKey.D_01,
        StudyPolicyKey.D_02,
        StudyPolicyKey.D_09,
        StudyPolicyKey.D_10,
        StudyPolicyKey.D_12,
        StudyPolicyKey.D_13,
    )
    owners = {}
    versions = {}
    for key in keys:
        policy = await _seed_approved_policy(db, org, admin_user.id, key)
        versions[key] = policy.id
        if key == StudyPolicyKey.D_10 and expired_ai:
            policy.effective_to = datetime.now(UTC) - timedelta(hours=1)
            db.add(policy)
        for responsibility in POLICY_REQUIRED_RESPONSIBILITIES[key]:
            if responsibility not in owners:
                owners[responsibility] = await _seed_active_owner(
                    db, org, admin_user.id, admin_user.id, responsibility
                )
            db.add(
                StudyPolicyApproval(
                    org_id=org.id,
                    policy_version_id=policy.id,
                    responsibility=responsibility.value,
                    approver_user_id=admin_user.id,
                    owner_assignment_id=owners[responsibility].id,
                    evidence_reference="synthetic-adaptive-approval",
                    rationale="Synthetic approval fixture",
                    approved_at=datetime.now(UTC),
                )
            )
        await db.commit()

    decision = await get_adaptive_availability(db, org, regular_user)
    assert decision.enabled is True
    assert decision.learner_eligible is False
    assert decision.allowed_actions == []
    assert decision.denials == {
        "diagnostic": "RESOURCE_APPROVAL_REQUIRED",
        "guided_ai": "POLICY_NOT_APPROVED"
        if expired_ai
        else "RESOURCE_APPROVAL_REQUIRED",
        "report": "POLICY_NOT_APPROVED",
    }
    profile = StudyLearnerProfile(
        org_id=org.id,
        user_id=regular_user.id,
        created_by_user_id=admin_user.id,
        status="active",
        control_mode="dependent",
    )
    db.add(profile)
    await db.flush()
    db.add(
        StudyEligibilityDecision(
            org_id=org.id,
            learner_profile_id=profile.id,
            eligibility_kind="learning",
            outcome="eligible",
            reviewer_user_id=admin_user.id,
            owner_assignment_id=owners[StudyOwnerFunction.EDUCATION].id,
            policy_version_ids=[
                versions[key]
                for key in (
                    StudyPolicyKey.D_01,
                    StudyPolicyKey.D_02,
                    StudyPolicyKey.D_12,
                )
            ],
            effective_from=datetime.now(UTC) - timedelta(minutes=1),
            evidence_reference="synthetic-learning-eligibility",
        )
    )
    await db.commit()
    learner_decision = await get_adaptive_availability(db, org, regular_user)
    assert learner_decision.learner_eligible is True
    assert learner_decision.allowed_actions == []
    assert learner_decision.denials == {
        "diagnostic": "DEPENDENT_AUTHORITY_REQUIRED",
        "guided_ai": "POLICY_NOT_APPROVED"
        if expired_ai
        else "DEPENDENT_AUTHORITY_REQUIRED",
        "report": "POLICY_NOT_APPROVED",
    }
    assert (
        await get_adaptive_availability(db, org, admin_user)
    ).learner_eligible is False


async def test_availability_http_requires_session_and_membership(
    db, org, regular_user, monkeypatch
):
    """Override identity lookup only. Run the auth, membership and route dependencies."""
    app = FastAPI()
    app.include_router(v1_router)
    app.dependency_overrides[get_db_session] = lambda: db
    current_user = AsyncMock(return_value=AnonymousUser())
    monkeypatch.setattr(auth, "get_current_user", current_user)
    monkeypatch.setattr(
        adaptive, "get_session_provenance", lambda: SessionProvenance(amr="password")
    )
    path = f"/api/v1/studytomy/adaptive/orgs/{org.org_uuid}/availability"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get(path)).status_code == 401
        current_user.return_value = APITokenUser(id=regular_user.id, org_id=org.id)
        assert (await client.get(path)).status_code == 403
        current_user.return_value = regular_user.model_copy(update={"id": 4321})
        denied = await client.get(path)
        assert denied.status_code == 404
        missing = await client.get(
            "/api/v1/studytomy/adaptive/orgs/org_missing/availability"
        )
        assert denied.json() == missing.json()
        assert denied.headers["Cache-Control"] == "private, no-store"

        current_user.return_value = regular_user
        allowed = await client.get(path)
        assert allowed.status_code == 200
        assert allowed.headers["Cache-Control"] == "private, no-store"
        assert allowed.json() == {
            "enabled": False,
            "learner_eligible": False,
            "allowed_actions": [],
            "denials": dict.fromkeys(
                ("diagnostic", "guided_ai", "report"), "ADAPTIVE_DISABLED"
            ),
        }


async def test_report_entry_points_reject_non_session_and_nonmember_callers(
    db, org, regular_user, monkeypatch
):
    app = FastAPI()
    app.include_router(v1_router)
    app.dependency_overrides[get_db_session] = lambda: db
    current_user = AsyncMock()
    monkeypatch.setattr(auth, "get_current_user", current_user)
    monkeypatch.setattr(
        adaptive, "get_session_provenance", lambda: SessionProvenance(amr="password")
    )
    report = "report_00000000-0000-0000-0000-000000000001"
    learner = "00000000-0000-0000-0000-000000000002"
    curriculum = "curriculum_00000000-0000-0000-0000-000000000003"
    staff = f"staff/reports/{report}"
    entries = (
        ("GET", "me/reports", None),
        ("GET", f"reports/{report}", None),
        (
            "GET",
            f"staff/learners/{learner}/reports?curriculum_revision_uuid={curriculum}",
            None,
        ),
        ("GET", staff, None),
        ("GET", f"{staff}/recipients?expected_version=1", None),
        (
            "POST",
            f"{staff}/recipient-preview",
            {"expected_version": 1, "recipient_grant_uuid": "synthetic"},
        ),
        (
            "POST",
            f"{staff}/publish",
            {"expected_version": 1, "recipient_grant_uuids": ["synthetic"]},
        ),
        (
            "POST",
            f"reports/{report}/review-requests",
            {"expected_version": 1, "reason": "other"},
        ),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for actor, status_code in (
            (AnonymousUser(), 401),
            (APITokenUser(id=regular_user.id, org_id=org.id), 403),
            (regular_user.model_copy(update={"id": 4321}), 404),
        ):
            current_user.return_value = actor
            for method, path, body in entries:
                response = await client.request(
                    method,
                    f"/api/v1/studytomy/adaptive/orgs/{org.org_uuid}/{path}",
                    json=body,
                )
                assert response.status_code == status_code, (
                    method,
                    path,
                    response.text,
                )
                if status_code == 404:
                    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.studytomy_postgres
async def test_postgres_toggle_cannot_enable_actions_without_approval(
    postgres_test_rows,
):
    rows = postgres_test_rows
    async with rows.session_factory() as db:
        org = await db.get(Organization, rows.org_id)
        actor = PublicUser.model_validate(await db.get(User, rows.user_id))
        db.add(
            OrganizationConfig(
                org_id=org.id,
                config={"admin_toggles": {"studytomy_adaptive_enabled": True}},
            )
        )
        await db.flush()
        result = await get_adaptive_availability(db, org, actor)

    assert result.enabled is True
    assert result.learner_eligible is False
    assert result.allowed_actions == []
    assert set(result.denials.values()) == {"POLICY_NOT_APPROVED"}
