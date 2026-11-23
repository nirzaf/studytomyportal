"""TASK-013 learner-controlled sharing and guardian-separation regressions."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.db.studytomy.family import (
    StudyLearnerControlMode,
    StudyLearnerProfile,
    StudyLearnerProfileStatus,
    StudySharingGrant,
    StudySharingGrantCreate,
    StudySharingGrantStatus,
    StudySharingGrantWithdrawal,
)
from src.db.studytomy.governance import (
    POLICY_REQUIRED_DECISION_AREAS,
    StudyBusinessEvent,
    StudyPolicyBody,
    StudyPolicyDecisionState,
    StudyPolicyDecisionValue,
    StudyPolicyKey,
)
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.services.studytomy import authority, learners, sharing

CURRENT_POLICY_IDS = [41, 42, 43]
APPROVED_SCOPE_CODES = {"progress_summary", "assessment_feedback"}


def _request(recipient_uuid: str, *, scopes=None, valid_to=None) -> StudySharingGrantCreate:
    return StudySharingGrantCreate(
        recipient_user_uuid=recipient_uuid,
        scope_codes=scopes or ["progress_summary"],
        valid_to=valid_to or (datetime.now(timezone.utc) + timedelta(days=30)),
    )


async def _learner_identity(db, org, user_role, *, control_mode="independent"):
    learner = User(
        username="sharing-learner",
        first_name="Synthetic",
        last_name="Learner",
        email="sharing-learner@example.test",
        password="hashed-test-password",
        user_uuid="user_sharing_learner",
        creation_date="2026-11-16T00:00:00Z",
        update_date="2026-11-16T00:00:00Z",
    )
    db.add(learner)
    await db.flush()
    db.add(
        UserOrganization(
            user_id=learner.id,
            org_id=org.id,
            role_id=user_role.id,
            creation_date="2026-11-16T00:00:00Z",
            update_date="2026-11-16T00:00:00Z",
        )
    )
    profile = StudyLearnerProfile(
        org_id=org.id,
        user_id=learner.id,
        status=StudyLearnerProfileStatus.ACTIVE.value,
        control_mode=control_mode,
        access_version=1,
        created_by_user_id=learner.id,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return PublicUser(
        id=learner.id,
        username=learner.username,
        first_name=learner.first_name,
        last_name=learner.last_name,
        email=learner.email,
        user_uuid=learner.user_uuid,
    ), profile


def _patch_current_sharing_policy(monkeypatch):
    async def current_context(*_args, **_kwargs):
        return CURRENT_POLICY_IDS

    async def current_policies(*_args, **_kwargs):
        return [SimpleNamespace(id=policy_id, body={}) for policy_id in CURRENT_POLICY_IDS]

    monkeypatch.setattr(sharing, "_current_independent_context", current_context)
    monkeypatch.setattr(sharing, "_current_policies", current_policies)
    monkeypatch.setattr(
        sharing,
        "_sharing_policy_scope",
        lambda _policies: (CURRENT_POLICY_IDS, set(APPROVED_SCOPE_CODES)),
    )


class TestIndependentSharing:
    def test_request_requires_explicit_period_and_opaque_member_reference(self, regular_user):
        with pytest.raises(ValueError):
            StudySharingGrantCreate(
                recipient_user_uuid=regular_user.user_uuid,
                scope_codes=["progress_summary"],
                valid_to=datetime.now() + timedelta(days=5),
            )
        with pytest.raises(ValueError):
            StudySharingGrantCreate(
                recipient_user_uuid="email@example.test",
                scope_codes=["progress_summary"],
                valid_to=datetime.now(timezone.utc) + timedelta(days=5),
            )

    async def test_create_is_policy_gated_without_creating_a_row(self, db, org, regular_user, user_role):
        learner, _profile = await _learner_identity(db, org, user_role)
        with pytest.raises(HTTPException) as missing_policy:
            await sharing.create_my_sharing_grant(
                db,
                org,
                learner,
                _request(regular_user.user_uuid),
                "sharing-no-policy",
            )
        assert missing_policy.value.status_code == 409
        assert missing_policy.value.detail["code"] == "POLICY_NOT_APPROVED"
        assert await db.scalar(select(StudySharingGrant.id)) is None

    async def test_learner_can_grant_replay_and_withdraw_scoped_sharing(
        self, db, org, regular_user, user_role, monkeypatch
    ):
        _patch_current_sharing_policy(monkeypatch)
        learner, profile = await _learner_identity(db, org, user_role)
        request = _request(regular_user.user_uuid)

        grant = await sharing.create_my_sharing_grant(
            db, org, learner, request, "sharing-create-key"
        )
        replay = await sharing.create_my_sharing_grant(
            db, org, learner, request, "sharing-create-key"
        )
        assert grant.grant_uuid == replay.grant_uuid
        assert grant.scope_codes == ["progress_summary"]
        assert grant.recipient_user_uuid == regular_user.user_uuid
        assert grant.status == StudySharingGrantStatus.ACTIVE
        assert grant.policy_version_ids == CURRENT_POLICY_IDS
        assert profile.access_version == 2

        assert await sharing.has_active_independent_sharing_scope(
            db, org, profile, regular_user.id, "progress_summary"
        )
        assert not await sharing.has_active_independent_sharing_scope(
            db, org, profile, regular_user.id, "assessment_feedback"
        )

        withdrawn = await sharing.withdraw_my_sharing_grant(
            db,
            org,
            learner,
            grant.grant_uuid,
            expected_version=1,
            idempotency_key="sharing-withdraw-key",
        )
        withdrawal_replay = await sharing.withdraw_my_sharing_grant(
            db,
            org,
            learner,
            grant.grant_uuid,
            expected_version=1,
            idempotency_key="sharing-withdraw-key",
        )
        assert withdrawn.status == StudySharingGrantStatus.WITHDRAWN
        assert withdrawn.authority_version == 2
        assert withdrawal_replay.grant_uuid == grant.grant_uuid
        assert not await sharing.has_active_independent_sharing_scope(
            db, org, profile, regular_user.id, "progress_summary"
        )
        assert len((await db.execute(select(StudyBusinessEvent))).scalars().all()) == 2

        with pytest.raises(HTTPException) as stale:
            await sharing.withdraw_my_sharing_grant(
                db,
                org,
                learner,
                grant.grant_uuid,
                expected_version=1,
                idempotency_key="sharing-withdraw-stale",
            )
        assert stale.value.status_code == 409
        assert stale.value.detail["code"] == "STALE_SHARING_VERSION"

    async def test_recipient_must_be_another_current_org_member(
        self, db, org, regular_user, admin_user, user_role, monkeypatch
    ):
        _patch_current_sharing_policy(monkeypatch)
        learner, _profile = await _learner_identity(db, org, user_role)
        outsider = User(
            username="sharing-outsider",
            first_name="Synthetic",
            last_name="Outsider",
            email="sharing-outsider@example.test",
            password="hashed-test-password",
            user_uuid="user_sharing_outsider",
            creation_date="2026-11-16T00:00:00Z",
            update_date="2026-11-16T00:00:00Z",
        )
        db.add(outsider)
        await db.commit()
        with pytest.raises(HTTPException) as missing_member:
            await sharing.create_my_sharing_grant(
                db,
                org,
                learner,
                _request(outsider.user_uuid),
                "sharing-outsider-key",
            )
        assert missing_member.value.status_code == 404
        assert await db.scalar(select(StudySharingGrant.id)) is None

        with pytest.raises(HTTPException) as self_grant:
            await sharing.create_my_sharing_grant(
                db,
                org,
                learner,
                _request(learner.user_uuid),
                "sharing-self-key",
            )
        assert self_grant.value.status_code == 404

    async def test_nonindependent_profile_cannot_start_sharing(
        self, db, org, user_role, regular_user, monkeypatch
    ):
        learner, _profile = await _learner_identity(
            db, org, user_role, control_mode=StudyLearnerControlMode.DEPENDENT.value
        )
        with pytest.raises(HTTPException) as unavailable:
            await sharing.create_my_sharing_grant(
                db,
                org,
                learner,
                _request(regular_user.user_uuid),
                "sharing-dependent-key",
            )
        assert unavailable.value.detail["code"] == "INDEPENDENT_SHARING_UNAVAILABLE"

    async def test_independent_or_unresolved_control_mode_denies_guardian_authority(
        self, db, org, user_role, regular_user
    ):
        _learner, profile = await _learner_identity(
            db, org, user_role, control_mode=StudyLearnerControlMode.INDEPENDENT.value
        )
        assert not await authority.has_active_guardian_scope(
            db, org, profile, regular_user.id, "progress_summary"
        )
        profile.control_mode = StudyLearnerControlMode.UNRESOLVED.value
        assert not await authority.has_active_guardian_scope(
            db, org, profile, regular_user.id, "progress_summary"
        )

    def test_independent_transition_requires_explicit_d01_d03_d12_values(self):
        def make_policy(policy_key, specified_areas):
            decision_items = {}
            for area in POLICY_REQUIRED_DECISION_AREAS[policy_key]:
                if area in specified_areas:
                    value = ["progress_summary"] if area == "parent_report_scope" else {"reviewed": True}
                    decision_items[area] = StudyPolicyDecisionValue(
                        state=StudyPolicyDecisionState.SPECIFIED,
                        value=value,
                        rationale="Synthetic fixture only.",
                        evidence_reference=f"TEST-{policy_key.value}-{area}",
                    )
                else:
                    decision_items[area] = StudyPolicyDecisionValue(
                        state=StudyPolicyDecisionState.NOT_APPLICABLE,
                        rationale="Synthetic fixture only.",
                        evidence_reference=f"TEST-{policy_key.value}-{area}",
                    )
            return SimpleNamespace(
                policy_key=policy_key.value,
                body=StudyPolicyBody(decision_items=decision_items).model_dump(mode="json"),
            )

        policies = [
            make_policy(StudyPolicyKey.D_01, {"independent_participation"}),
            make_policy(StudyPolicyKey.D_03, {"guardian_authority"}),
            make_policy(
                StudyPolicyKey.D_12,
                {"information_uses", "consent", "parent_report_scope"},
            ),
        ]
        with pytest.raises(HTTPException) as unresolved:
            learners._require_independent_transition_decisions(policies)
        assert unresolved.value.detail["code"] == "POLICY_DECISION_UNRESOLVED"

        policies[1] = make_policy(
            StudyPolicyKey.D_03, {"guardian_authority", "adult_transition"}
        )
        learners._require_independent_transition_decisions(policies)

    async def test_withdrawal_body_needs_positive_expected_version(self):
        assert StudySharingGrantWithdrawal(expected_version=1).expected_version == 1
        with pytest.raises(ValueError):
            StudySharingGrantWithdrawal(expected_version=0)


class TestFamilyReviewWorkspace:
    async def test_queue_requires_explicit_current_d04_support_decisions(
        self, db, org, admin_user, monkeypatch
    ):
        async def relationship_policies(*_args, **_kwargs):
            return [], {"progress_summary"}

        async def support_policy(*_args, **_kwargs):
            return [SimpleNamespace(id=55, body={"decision_items": {}})]

        monkeypatch.setattr(authority, "_current_relationship_policies", relationship_policies)
        monkeypatch.setattr(learners, "_current_policies", support_policy)
        with pytest.raises(HTTPException) as missing_support_decisions:
            await authority.list_family_review_queue(db, org, admin_user)
        assert missing_support_decisions.value.status_code == 409
        assert (
            missing_support_decisions.value.detail["code"]
            == "FAMILY_REVIEW_SUPPORT_POLICY_INCOMPLETE"
        )
