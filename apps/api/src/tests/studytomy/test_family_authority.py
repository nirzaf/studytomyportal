"""Focused default-deny and scoped-decision regressions for TASK-011."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from src.db.studytomy.family import (
    StudyLearnerControlMode,
    StudyLearnerProfile,
    StudyLearnerProfileStatus,
    StudyRelationship,
    StudyRelationshipDecisionCreate,
    StudyRelationshipDecisionOutcome,
    StudyRelationshipRequestCreate,
    StudyRelationshipRestrictionAction,
    StudyRelationshipRestrictionCreate,
    StudyRelationshipStatus,
    StudySharingGrant,
)
from src.db.studytomy.governance import (
    POLICY_REQUIRED_DECISION_AREAS,
    StudyOwnerAssignment,
    StudyOwnerFunction,
    StudyOwnerStatus,
    StudyPolicyBody,
    StudyPolicyDecisionState,
    StudyPolicyDecisionValue,
    StudyPolicyKey,
    StudyPolicyStatus,
    StudyPolicyVersion,
    StudyStaffScope,
)
from src.db.user_organizations import UserOrganization
from src.db.users import User
from src.services.studytomy import authority
from src.services.users.users import delete_user_by_id

POLICY_IDS = [31, 32, 33]
APPROVED_SCOPES = {"progress_summary", "assessment_feedback"}


async def _policy_snapshot(_db, _org, _now):
    return [SimpleNamespace(id=policy_id) for policy_id in POLICY_IDS], set(APPROVED_SCOPES)


async def _make_profile(db, org, admin_user, regular_user):
    learner = User(
        id=3,
        username="learner-authority-test",
        first_name="Test",
        last_name="Learner",
        email="learner-authority-test@example.test",
        password="hashed-test-password",
        user_uuid="user_learner_authority_test",
        creation_date="2026-11-16T00:00:00Z",
        update_date="2026-11-16T00:00:00Z",
    )
    db.add(learner)
    role_id = await db.scalar(
        select(UserOrganization.role_id).where(
            UserOrganization.user_id == regular_user.id,
            UserOrganization.org_id == org.id,
        )
    )
    db.add(
        UserOrganization(
            user_id=learner.id,
            org_id=org.id,
            role_id=role_id,
            creation_date="2026-11-16T00:00:00Z",
            update_date="2026-11-16T00:00:00Z",
        )
    )
    profile = StudyLearnerProfile(
        org_id=org.id,
        user_id=learner.id,
        status=StudyLearnerProfileStatus.ACTIVE.value,
        control_mode=StudyLearnerControlMode.DEPENDENT.value,
        access_version=1,
        created_by_user_id=admin_user.id,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


def _request(*, scopes=None):
    return StudyRelationshipRequestCreate(
        requested_scopes=scopes or ["progress_summary", "assessment_feedback"],
        evidence_reference="case_0123456789abcdef",
    )


@pytest.mark.parametrize("record_kind", ["relationship", "sharing"])
async def test_family_history_blocks_account_deletion_before_cleanup(
    db, org, admin_user, regular_user, mock_request, record_kind
):
    profile = await _make_profile(db, org, admin_user, regular_user)
    if record_kind == "relationship":
        record = StudyRelationship(
            org_id=org.id,
            learner_profile_id=profile.id,
            adult_user_id=regular_user.id,
            requested_by_user_id=regular_user.id,
            requested_scopes=["progress_summary"],
            request_evidence_reference="case_0123456789abcdef",
            request_policy_version_ids=POLICY_IDS,
        )
    else:
        now = datetime.now(timezone.utc)
        record = StudySharingGrant(
            org_id=org.id,
            learner_profile_id=profile.id,
            recipient_user_id=regular_user.id,
            created_by_user_id=profile.user_id,
            scope_codes=["progress_summary"],
            policy_version_ids=POLICY_IDS,
            valid_from=now - timedelta(days=2),
            valid_to=now - timedelta(days=1),
        )
    db.add(record)
    await db.commit()
    with (
        patch("src.services.users.users.rbac_check", new_callable=AsyncMock),
        pytest.raises(HTTPException) as blocked,
    ):
        await delete_user_by_id(mock_request, db, regular_user, regular_user.id)
    assert blocked.value.status_code == 409
    await db.refresh(record)
    assert await db.get(User, regular_user.id) is not None


async def _create_request(db, org, regular_user, profile, *, key="request-key"):
    return await authority.create_relationship_request(
        db,
        org,
        regular_user,
        profile.learner_uuid,
        _request(),
        key,
    )


async def _seed_reviewer_scope(db, org, reviewer, requester, profile):
    now = datetime.now(timezone.utc)
    owner = StudyOwnerAssignment(
        org_id=org.id,
        user_id=reviewer.id,
        responsibility=StudyOwnerFunction.FAMILY_SERVICES.value,
        scope_kind="organization",
        scope_uuid=org.org_uuid,
        status=StudyOwnerStatus.ACTIVE.value,
        evidence_reference="TEST-FAMILY-SERVICE-OWNER",
        effective_from=now - timedelta(minutes=1),
        assigned_by_user_id=requester.id,
    )
    db.add(owner)
    await db.flush()
    db.add(
        StudyStaffScope(
            org_id=org.id,
            assignment_id=owner.id,
            capability="family_authority_review",
            scope_kind="learner",
            scope_uuid=profile.learner_uuid,
            is_exclusion=False,
            status="active",
            evidence_reference="TEST-LEARNER-SCOPE",
            created_by_user_id=requester.id,
        )
    )
    await db.commit()
    return owner


class TestRelationshipAuthority:
    async def test_restriction_transition_fails_closed_without_approved_d11(
        self, db, org, admin_user
    ):
        now = datetime.now(timezone.utc)
        policy_values = {
            StudyPolicyKey.D_03: {"guardian_authority": {"verification": "human"}},
            StudyPolicyKey.D_12: {
                "information_uses": {"purpose": "family-authority-review"},
                "consent": {"required": True},
                "parent_report_scope": ["progress_summary"],
            },
            StudyPolicyKey.D_13: {
                "retention": {"learner_evidence_retention_category": "family_records"},
                "record_access": {"assigned_reviewer_only": True},
            },
        }
        for policy_key, specified in policy_values.items():
            items = {
                area: StudyPolicyDecisionValue(
                    state=StudyPolicyDecisionState.NOT_APPLICABLE,
                    rationale="Synthetic test fixture only.",
                    evidence_reference=f"TEST-{policy_key.value}-{area}",
                )
                for area in POLICY_REQUIRED_DECISION_AREAS[policy_key]
            }
            items.update(
                {
                    area: StudyPolicyDecisionValue(
                        state=StudyPolicyDecisionState.SPECIFIED,
                        value=value,
                        rationale="Synthetic test fixture only.",
                        evidence_reference=f"TEST-{policy_key.value}-{area}",
                    )
                    for area, value in specified.items()
                }
            )
            db.add(
                StudyPolicyVersion(
                    org_id=org.id,
                    policy_key=policy_key.value,
                    scope_kind="organization",
                    scope_uuid=org.org_uuid,
                    version=1,
                    status=StudyPolicyStatus.APPROVED.value,
                    body=StudyPolicyBody(decision_items=items).model_dump(mode="json"),
                    evidence_reference=f"TEST-{policy_key.value}",
                    effective_from=now - timedelta(minutes=1),
                    created_by_user_id=admin_user.id,
                    approved_by_user_id=admin_user.id,
                    approved_at=now,
                    created_at=now,
                )
            )
        await db.commit()

        with pytest.raises(HTTPException) as missing_d11:
            await authority._current_restriction_policies(db, org, now)
        assert missing_d11.value.status_code == 409
        assert missing_d11.value.detail["code"] == "POLICY_NOT_APPROVED"

    async def test_pending_request_has_no_authority_and_replays_safely(
        self, db, org, admin_user, regular_user, monkeypatch
    ):
        monkeypatch.setattr(authority, "_current_relationship_policies", _policy_snapshot)
        profile = await _make_profile(db, org, admin_user, regular_user)

        first = await _create_request(db, org, regular_user, profile)
        replay = await _create_request(db, org, regular_user, profile)

        assert first.relationship_uuid == replay.relationship_uuid
        assert first.status == StudyRelationshipStatus.PENDING
        assert first.approved_scopes == []
        assert first.authority_version == 1
        assert not await authority.has_active_guardian_scope(
            db, org, profile, regular_user.id, "progress_summary"
        )

    async def test_assigned_review_accepts_only_a_requested_scope_and_checks_version(
        self, db, org, admin_user, regular_user, monkeypatch
    ):
        monkeypatch.setattr(authority, "_current_relationship_policies", _policy_snapshot)
        profile = await _make_profile(db, org, admin_user, regular_user)
        pending = await _create_request(db, org, regular_user, profile)
        await _seed_reviewer_scope(db, org, admin_user, regular_user, profile)

        authorization = AsyncMock(return_value=SimpleNamespace(policy_version_ids=POLICY_IDS))
        monkeypatch.setattr(authority, "authorize_studytomy_staff_action", authorization)

        valid_from = datetime.now(timezone.utc) + timedelta(minutes=5)
        valid_to = valid_from + timedelta(days=30)
        decision = StudyRelationshipDecisionCreate(
            outcome=StudyRelationshipDecisionOutcome.ACCEPT,
            expected_version=1,
            approved_scopes=["progress_summary"],
            valid_from=valid_from,
            valid_to=valid_to,
            evidence_reference="case_fedcba9876543210",
            reason_code="AUTHORITY_VERIFIED",
        )
        accepted = await authority.decide_relationship_request(
            db,
            org,
            admin_user,
            profile.learner_uuid,
            pending.relationship_uuid,
            decision,
            "decision-key",
        )

        assert accepted.status == StudyRelationshipStatus.ACCEPTED
        assert accepted.approved_scopes == ["progress_summary"]
        assert accepted.authority_version == 2
        call_kwargs = authorization.await_args.kwargs
        assert call_kwargs["subject_user_id"] == profile.user_id
        assert call_kwargs["excluded_user_ids"] == {profile.user_id, regular_user.id}
        effective_now = valid_from + timedelta(seconds=1)
        assert await authority.has_active_guardian_scope(
            db, org, profile, regular_user.id, "progress_summary", now=effective_now
        )
        assert await authority.has_active_guardian_scope(
            db, org, profile, regular_user.id, "progress_summary",
            now=effective_now, relationship_binding=(accepted.relationship_uuid, 2),
        )
        for binding in ((accepted.relationship_uuid, 1), ("other-relationship", 2)):
            assert not await authority.has_active_guardian_scope(
                db, org, profile, regular_user.id, "progress_summary",
                now=effective_now, relationship_binding=binding,
            )
        assert not await authority.has_active_guardian_scope(
            db, org, profile, regular_user.id, "assessment_feedback", now=effective_now
        )
        assert not await authority.has_active_guardian_scope(
            db, org, profile, regular_user.id, "progress_summary", now=valid_to
        )

        stale = decision.model_copy(update={"expected_version": 1})
        with pytest.raises(HTTPException) as stale_exc:
            await authority.decide_relationship_request(
                db,
                org,
                admin_user,
                profile.learner_uuid,
                pending.relationship_uuid,
                stale,
                "stale-decision-key",
            )
        assert stale_exc.value.status_code == 409
        assert stale_exc.value.detail["code"] == "STALE_AUTHORITY_VERSION"

    async def test_decision_cannot_widen_the_requested_or_policy_scope(
        self, db, org, admin_user, regular_user, monkeypatch
    ):
        monkeypatch.setattr(authority, "_current_relationship_policies", _policy_snapshot)
        profile = await _make_profile(db, org, admin_user, regular_user)
        pending = await _create_request(db, org, regular_user, profile)
        await _seed_reviewer_scope(db, org, admin_user, regular_user, profile)
        monkeypatch.setattr(
            authority,
            "authorize_studytomy_staff_action",
            AsyncMock(return_value=SimpleNamespace(policy_version_ids=POLICY_IDS)),
        )
        now = datetime.now(timezone.utc)
        decision = StudyRelationshipDecisionCreate(
            outcome=StudyRelationshipDecisionOutcome.ACCEPT,
            expected_version=1,
            approved_scopes=["billing_access"],
            valid_from=now + timedelta(minutes=5),
            valid_to=now + timedelta(days=30),
            evidence_reference="case_fedcba9876543210",
            reason_code="AUTHORITY_VERIFIED",
        )
        with pytest.raises(HTTPException) as scope_exc:
            await authority.decide_relationship_request(
                db,
                org,
                admin_user,
                profile.learner_uuid,
                pending.relationship_uuid,
                decision,
                "invalid-scope-decision",
            )
        assert scope_exc.value.status_code == 422
        assert scope_exc.value.detail["code"] == "SCOPE_NOT_REQUESTED_OR_APPROVED"
        await db.rollback()

    async def test_denied_relationship_never_resolves_as_a_grant(
        self, db, org, admin_user, regular_user, monkeypatch
    ):
        monkeypatch.setattr(authority, "_current_relationship_policies", _policy_snapshot)
        profile = await _make_profile(db, org, admin_user, regular_user)
        pending = await _create_request(db, org, regular_user, profile)
        await _seed_reviewer_scope(db, org, admin_user, regular_user, profile)
        monkeypatch.setattr(
            authority,
            "authorize_studytomy_staff_action",
            AsyncMock(return_value=SimpleNamespace(policy_version_ids=POLICY_IDS)),
        )
        denial = StudyRelationshipDecisionCreate(
            outcome=StudyRelationshipDecisionOutcome.DENY,
            expected_version=1,
            evidence_reference="case_fedcba9876543210",
            reason_code="AUTHORITY_NOT_VERIFIED",
        )

        denied = await authority.decide_relationship_request(
            db,
            org,
            admin_user,
            profile.learner_uuid,
            pending.relationship_uuid,
            denial,
            "denial-key",
        )

        assert denied.status == StudyRelationshipStatus.DENIED
        assert denied.approved_scopes == []
        assert denied.authority_version == 2
        assert not await authority.has_active_guardian_scope(
            db, org, profile, regular_user.id, "progress_summary"
        )

    async def test_dispute_bumps_access_versions_and_suppresses_the_scope(
        self, db, org, admin_user, regular_user, monkeypatch
    ):
        monkeypatch.setattr(authority, "_current_relationship_policies", _policy_snapshot)
        profile = await _make_profile(db, org, admin_user, regular_user)
        pending = await _create_request(db, org, regular_user, profile)
        await _seed_reviewer_scope(db, org, admin_user, regular_user, profile)
        authorization = AsyncMock(return_value=SimpleNamespace(policy_version_ids=POLICY_IDS))
        monkeypatch.setattr(authority, "authorize_studytomy_staff_action", authorization)
        valid_from = datetime.now(timezone.utc) + timedelta(minutes=5)
        accepted = await authority.decide_relationship_request(
            db,
            org,
            admin_user,
            profile.learner_uuid,
            pending.relationship_uuid,
            StudyRelationshipDecisionCreate(
                outcome=StudyRelationshipDecisionOutcome.ACCEPT,
                expected_version=1,
                approved_scopes=["progress_summary"],
                valid_from=valid_from,
                valid_to=valid_from + timedelta(days=30),
                evidence_reference="case_fedcba9876543210",
                reason_code="AUTHORITY_VERIFIED",
            ),
            "initial-decision-key",
        )
        assert accepted.status == StudyRelationshipStatus.ACCEPTED

        other_guardian = User(
            username="second-guardian-authority-test",
            first_name="Synthetic",
            last_name="Guardian",
            email="second-guardian@example.test",
            user_uuid="user_second_guardian_authority_test",
            creation_date="2026-11-16T00:00:00Z",
            update_date="2026-11-16T00:00:00Z",
        )
        db.add(other_guardian)
        await db.flush()
        role_id = await db.scalar(select(UserOrganization.role_id).where(
            UserOrganization.user_id == regular_user.id,
            UserOrganization.org_id == org.id,
        ))
        db.add(UserOrganization(
            user_id=other_guardian.id, org_id=org.id, role_id=role_id,
            creation_date="", update_date="",
        ))
        await db.commit()
        other_pending = await _create_request(db, org, other_guardian, profile, key="second-request")
        other_accepted = await authority.decide_relationship_request(
            db, org, admin_user, profile.learner_uuid, other_pending.relationship_uuid,
            StudyRelationshipDecisionCreate(
                outcome=StudyRelationshipDecisionOutcome.ACCEPT,
                expected_version=1,
                approved_scopes=["assessment_feedback"],
                valid_from=valid_from,
                valid_to=valid_from + timedelta(days=30),
                evidence_reference="case_abcdef9876543210",
                reason_code="AUTHORITY_VERIFIED",
            ),
            "second-decision",
        )
        now = valid_from + timedelta(seconds=1)
        first_binding = (accepted.relationship_uuid, accepted.authority_version)
        second_binding = (other_accepted.relationship_uuid, other_accepted.authority_version)
        assert await authority.has_active_guardian_scope(
            db, org, profile, regular_user.id, "progress_summary", now=now, relationship_binding=first_binding
        )
        assert not await authority.has_active_guardian_scope(
            db, org, profile, other_guardian.id, "progress_summary", now=now, relationship_binding=first_binding
        )
        assert not await authority.has_active_guardian_scope(
            db, org, profile, regular_user.id, "assessment_feedback", now=now, relationship_binding=second_binding
        )

        async def restriction_policies(_db, _org, _now):
            return (
                [SimpleNamespace(id=31), SimpleNamespace(id=34), SimpleNamespace(id=32), SimpleNamespace(id=33)],
                set(APPROVED_SCOPES),
            )

        monkeypatch.setattr(authority, "_current_restriction_policies", restriction_policies)
        disputed = await authority.restrict_relationship_authority(
            db,
            org,
            admin_user,
            profile.learner_uuid,
            pending.relationship_uuid,
            StudyRelationshipRestrictionCreate(
                action=StudyRelationshipRestrictionAction.DISPUTE,
                expected_version=2,
                evidence_reference="case_abcdef0123456789",
                reason_code="AUTHORITY_DISPUTED",
            ),
            "dispute-key",
        )

        assert disputed.status == StudyRelationshipStatus.DISPUTED
        assert disputed.approved_scopes == []
        assert disputed.authority_version == 3
        assert profile.access_version == 4
        assert not await authority.has_active_guardian_scope(
            db,
            org,
            profile,
            regular_user.id,
            "progress_summary",
            now=valid_from + timedelta(seconds=1),
        )
        assert await authority.has_active_guardian_scope(
            db, org, profile, other_guardian.id, "assessment_feedback", now=now, relationship_binding=second_binding
        )
        assert authorization.await_args.kwargs["excluded_user_ids"] == {
            profile.user_id,
            regular_user.id,
        }

    def test_authority_request_and_decision_schemas_reject_unbounded_or_naive_inputs(self):
        with pytest.raises(ValidationError):
            _request(scopes=["progress summary"])

        now = datetime.now(timezone.utc)
        with pytest.raises(ValidationError):
            StudyRelationshipDecisionCreate(
                outcome=StudyRelationshipDecisionOutcome.ACCEPT,
                expected_version=1,
                approved_scopes=[],
                valid_from=now,
                valid_to=now + timedelta(days=1),
                evidence_reference="case_fedcba9876543210",
                reason_code="AUTHORITY_VERIFIED",
            )
        with pytest.raises(ValidationError):
            StudyRelationshipDecisionCreate(
                outcome=StudyRelationshipDecisionOutcome.ACCEPT,
                expected_version=1,
                approved_scopes=["progress_summary"],
                valid_from=datetime(2026, 11, 16),
                valid_to=datetime(2026, 12, 16, tzinfo=timezone.utc),
                evidence_reference="case_fedcba9876543210",
                reason_code="AUTHORITY_VERIFIED",
            )


async def test_private_family_routes_do_not_cache_success_or_denial(db, org, regular_user):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.core.events.database import get_db_session
    from src.routers.studytomy.family import router
    from src.security.api_token_utils import get_authenticated_non_api_token_user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_authenticated_non_api_token_user] = lambda: regular_user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://local.test") as client:
        own = await client.get(f"/orgs/{org.org_uuid}/my-relationships")
        assert own.status_code == 200, own.text
        assert own.json() == []
        assert own.headers["cache-control"] == "private, no-store"

        denied = await client.get("/orgs/org_nonexistent/my-relationships")
        assert denied.status_code == 404
        assert denied.headers["cache-control"] == "private, no-store"
