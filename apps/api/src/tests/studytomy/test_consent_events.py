"""Focused tests for append-only, policy-bound consent evidence."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from src.db.studytomy.family import (
    StudyLearnerControlMode,
    StudyLearnerProfile,
    StudyLearnerProfileStatus,
)
from src.db.studytomy.governance import (
    POLICY_REQUIRED_DECISION_AREAS,
    StudyPolicyBody,
    StudyPolicyDecisionState,
    StudyPolicyDecisionValue,
    StudyPolicyKey,
)
from src.db.studytomy.privacy import (
    StudyConsentChoice,
    StudyConsentEvent,
    StudyConsentEventCreate,
    StudyConsentPurpose,
)
from src.db.users import PublicUser
from src.router import v1_router
from src.services.studytomy import consents


def _public_user(user) -> PublicUser:
    return PublicUser(
        id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
        email=user.email,
        user_uuid=user.user_uuid,
    )


async def _profile(db, org, user, *, control_mode=StudyLearnerControlMode.INDEPENDENT.value):
    profile = StudyLearnerProfile(
        learner_uuid=f"learner_consent_{user.id}",
        org_id=org.id,
        user_id=user.id,
        status=StudyLearnerProfileStatus.ACTIVE.value,
        control_mode=control_mode,
        created_by_user_id=user.id,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


def _policy(policy_key: StudyPolicyKey, policy_id: int, purpose: StudyConsentPurpose):
    specified_values = {}
    if policy_key == StudyPolicyKey.D_01:
        specified_values["independent_participation"] = {"test_only": True}
    elif policy_key == StudyPolicyKey.D_03:
        specified_values["adult_transition"] = {"test_only": True}
    elif policy_key == StudyPolicyKey.D_12:
        specified_values["information_uses"] = {purpose.value: ["promotion"]}
        specified_values["consent"] = {
            "notice_versions": {purpose.value: "notice-2026-1"},
            "notice_texts": {
                purpose.value: "Synthetic test notice for consent regression only."
            },
        }
    elif policy_key == StudyPolicyKey.D_05:
        specified_values.update(
            {
                "prices": {"test_only": True},
                "access_periods": {"test_only": True},
                "learner_allocation": {"test_only": True},
            }
        )
    elif policy_key == StudyPolicyKey.D_10:
        for area in ("learner_eligibility", "supervision", "materials", "usage_limits", "review"):
            specified_values[area] = {"test_only": True}
    elif policy_key == StudyPolicyKey.D_11:
        for area in ("community_audiences", "adult_contact_boundaries", "moderation"):
            specified_values[area] = {"test_only": True}
    elif policy_key == StudyPolicyKey.D_13:
        for area in ("retention", "record_access", "correction", "removal"):
            specified_values[area] = {"test_only": True}
    elif policy_key == StudyPolicyKey.D_14:
        specified_values.update(
            {"contacts": {"test_only": True}, "marketing_choices": {"test_only": True}}
        )
    elif policy_key == StudyPolicyKey.D_16:
        specified_values.update(
            {"content_rights": {"test_only": True}, "review_approvals": {"test_only": True}}
        )
    elif policy_key == StudyPolicyKey.D_18:
        specified_values.update(
            {
                "customer_terms": {"test_only": True},
                "selected_market_requirements": {"test_only": True},
            }
        )

    decision_items = {}
    for area in POLICY_REQUIRED_DECISION_AREAS[policy_key]:
        value = specified_values.get(area)
        decision_items[area] = StudyPolicyDecisionValue(
            state=(
                StudyPolicyDecisionState.SPECIFIED
                if value is not None
                else StudyPolicyDecisionState.NOT_APPLICABLE
            ),
            value=value,
            rationale="Synthetic test policy only.",
            evidence_reference=f"TEST-{policy_key.value}-{area}",
        )
    return SimpleNamespace(
        id=policy_id,
        policy_key=policy_key.value,
        body=StudyPolicyBody(decision_items=decision_items).model_dump(mode="json"),
    )


def _patch_approved_context(
    monkeypatch,
    purpose=StudyConsentPurpose.MARKETING,
    *,
    patch_authority=True,
):
    keys = (
        StudyPolicyKey.D_01,
        StudyPolicyKey.D_03,
        StudyPolicyKey.D_12,
        *consents._PURPOSE_POLICY_KEYS[purpose],
    )
    policies = [_policy(key, index + 101, purpose) for index, key in enumerate(keys)]

    async def current_policies(_db, _org, requested_keys, _now):
        assert tuple(requested_keys) == keys
        return policies

    async def independent_learner(*_args, **_kwargs):
        return None

    monkeypatch.setattr(consents, "_current_policies", current_policies)
    if patch_authority:
        monkeypatch.setattr(consents, "_require_current_independent_learner", independent_learner)


class TestConsentEvents:
    def test_consent_routes_are_mounted(self):
        mounted = set()
        for include in v1_router.routes:
            context = getattr(include, "include_context", None)
            if context is None:
                continue
            for route in context.included_router.routes:
                path = getattr(route, "path", "")
                if "consent-events" not in path and "consent-notices" not in path:
                    continue
                mounted.update(
                    (f"{context.prefix}{path}", method)
                    for method in (getattr(route, "methods", None) or set())
                )
        assert mounted == {
            (
                "/api/v1/studytomy/orgs/{org_uuid}/learners/me/consent-notices/{purpose}",
                "GET",
            ),
            (
                "/api/v1/studytomy/orgs/{org_uuid}/learners/me/consent-events",
                "GET",
            ),
            (
                "/api/v1/studytomy/orgs/{org_uuid}/learners/me/consent-events",
                "POST",
            ),
            (
                "/api/v1/studytomy/orgs/{org_uuid}/learners/me/consent-events/{event_uuid}/withdraw",
                "POST",
            ),
            (
                "/api/v1/studytomy/orgs/{org_uuid}/learners/{learner_uuid}/consent-events",
                "GET",
            ),
            (
                "/api/v1/studytomy/orgs/{org_uuid}/learners/{learner_uuid}/consent-events",
                "POST",
            ),
            (
                "/api/v1/studytomy/orgs/{org_uuid}/learners/{learner_uuid}/consent-events/{event_uuid}/withdraw",
                "POST",
            ),
            (
                "/api/v1/studytomy/orgs/{org_uuid}/dependents/{learner_uuid}/consent-notices/{purpose}",
                "GET",
            ),
            (
                "/api/v1/studytomy/orgs/{org_uuid}/dependents/{learner_uuid}/consent-events",
                "GET",
            ),
            (
                "/api/v1/studytomy/orgs/{org_uuid}/dependents/{learner_uuid}/consent-events",
                "POST",
            ),
            (
                "/api/v1/studytomy/orgs/{org_uuid}/dependents/{learner_uuid}/consent-events/{event_uuid}/withdraw",
                "POST",
            ),
        }

    async def test_self_notice_read_returns_current_approved_copy(
        self, db, org, regular_user, monkeypatch
    ):
        await _profile(db, org, regular_user)
        _patch_approved_context(monkeypatch)

        notice = await consents.get_my_consent_notice(
            db,
            org,
            _public_user(regular_user),
            StudyConsentPurpose.MARKETING,
        )

        assert notice.notice_version == "notice-2026-1"
        assert notice.notice_text == "Synthetic test notice for consent regression only."
        assert notice.scope_codes == ["promotion"]
        assert notice.policy_version_ids
        assert await db.scalar(select(func.count(StudyConsentEvent.id))) == 0

    async def test_notice_read_cannot_resolve_another_accounts_profile(
        self, db, org, regular_user, admin_user
    ):
        await _profile(db, org, regular_user)

        with pytest.raises(HTTPException) as denied:
            await consents.get_my_consent_notice(
                db,
                org,
                _public_user(admin_user),
                StudyConsentPurpose.MARKETING,
            )

        assert denied.value.status_code == 404

    async def test_self_history_read_returns_only_authenticated_subject_events(
        self, db, org, regular_user, admin_user, monkeypatch
    ):
        profile = await _profile(db, org, regular_user)
        _patch_approved_context(monkeypatch)
        request = StudyConsentEventCreate(
            purpose="marketing",
            choice="decline",
            notice_version="notice-2026-1",
            scope_codes=["promotion"],
        )
        actor = _public_user(regular_user)
        event = await consents.record_my_consent_choice(
            db,
            org,
            actor,
            profile.learner_uuid,
            request,
            "consent-self-history-key",
        )

        history = await consents.list_my_consent_events_for_self(db, org, actor)
        assert [item.event_uuid for item in history] == [event.event_uuid]

        with pytest.raises(HTTPException) as denied:
            await consents.list_my_consent_events_for_self(
                db, org, _public_user(admin_user)
            )
        assert denied.value.status_code == 404

    async def test_self_choice_and_withdrawal_derive_subject_from_authenticated_actor(
        self, db, org, regular_user, admin_user, monkeypatch
    ):
        profile = await _profile(db, org, regular_user)
        _patch_approved_context(monkeypatch)
        actor = _public_user(regular_user)
        request = StudyConsentEventCreate(
            purpose="marketing",
            choice="accept",
            notice_version="notice-2026-1",
            scope_codes=["promotion"],
        )

        accepted = await consents.record_my_consent_choice_for_self(
            db, org, actor, request, "consent-self-route-key"
        )
        withdrawn = await consents.withdraw_my_consent_choice_for_self(
            db,
            org,
            actor,
            accepted.event_uuid,
            "consent-self-withdraw-route-key",
        )

        assert accepted.choice == StudyConsentChoice.ACCEPT
        assert withdrawn.choice == StudyConsentChoice.WITHDRAW
        assert withdrawn.withdraws_event_uuid == accepted.event_uuid
        assert profile.id is not None
        stored = (
            await db.execute(
                select(StudyConsentEvent)
                .where(StudyConsentEvent.event_uuid == accepted.event_uuid)
            )
        ).scalars().one()
        assert stored.learner_profile_id == profile.id
        assert stored.subject_user_id == regular_user.id
        assert stored.actor_user_id == regular_user.id

        with pytest.raises(HTTPException) as denied:
            await consents.record_my_consent_choice_for_self(
                db, org, _public_user(admin_user), request, "consent-other-actor-key"
            )
        assert denied.value.status_code == 404

    async def test_self_withdrawal_remains_available_after_current_policy_changes(
        self, db, org, regular_user, monkeypatch
    ):
        profile = await _profile(db, org, regular_user)
        _patch_approved_context(monkeypatch)
        actor = _public_user(regular_user)
        accepted = await consents.record_my_consent_choice_for_self(
            db,
            org,
            actor,
            StudyConsentEventCreate(
                purpose="marketing",
                choice="accept",
                notice_version="notice-2026-1",
                scope_codes=["promotion"],
            ),
            "consent-before-policy-change",
        )

        async def changed_policy(*_args, **_kwargs):
            raise HTTPException(status_code=409, detail="Current policy changed")

        monkeypatch.setattr(consents, "_current_policies", changed_policy)
        withdrawn = await consents.withdraw_my_consent_choice_for_self(
            db,
            org,
            actor,
            accepted.event_uuid,
            "consent-withdraw-after-policy-change",
        )

        assert withdrawn.choice == StudyConsentChoice.WITHDRAW
        assert withdrawn.withdraws_event_uuid == accepted.event_uuid
        assert profile.id is not None
        history = await consents.list_my_consent_events_for_self(db, org, actor)
        assert [event.choice for event in history] == [
            StudyConsentChoice.WITHDRAW,
            StudyConsentChoice.ACCEPT,
        ]

    def test_choice_requires_explicit_scope_and_opaque_notice(self):
        request = StudyConsentEventCreate(
            purpose=StudyConsentPurpose.MARKETING,
            choice=StudyConsentChoice.DECLINE,
            notice_version="notice-2026-1",
            scope_codes=["promotion"],
        )
        assert request.scope_codes == ["promotion"]
        with pytest.raises(ValueError):
            StudyConsentEventCreate(
                purpose="marketing",
                choice="withdraw",
                notice_version="notice-2026-1",
                scope_codes=["promotion"],
            )
        with pytest.raises(ValueError):
            StudyConsentEventCreate(
                purpose="marketing",
                choice="accept",
                notice_version="private notice copy",
                scope_codes=["promotion"],
            )
        with pytest.raises(ValueError):
            StudyConsentEventCreate(
                purpose="marketing",
                choice="accept",
                notice_version="notice-2026-1",
                scope_codes=["promotion", "promotion"],
            )

    async def test_pending_policy_denies_write_without_creating_event(
        self, db, org, regular_user
    ):
        profile = await _profile(db, org, regular_user)
        request = StudyConsentEventCreate(
            purpose="marketing",
            choice="accept",
            notice_version="notice-2026-1",
            scope_codes=["promotion"],
        )
        with pytest.raises(HTTPException) as denied:
            await consents.record_my_consent_choice(
                db,
                org,
                _public_user(regular_user),
                profile.learner_uuid,
                request,
                "consent-pending-policy",
            )
        assert denied.value.status_code == 409
        assert denied.value.detail["code"] == "POLICY_NOT_APPROVED"
        assert await db.scalar(select(func.count(StudyConsentEvent.id))) == 0
        assert profile.access_version == 1

    async def test_only_the_subject_can_read_their_history(self, db, org, regular_user, admin_user):
        profile = await _profile(db, org, regular_user)
        with pytest.raises(HTTPException) as denied:
            await consents.list_my_consent_events(
                db, org, _public_user(admin_user), profile.learner_uuid
            )
        assert denied.value.status_code == 404
        assert await db.scalar(select(func.count(StudyConsentEvent.id))) == 0

    async def test_dependent_learner_cannot_self_record_while_authority_is_unapproved(
        self, db, org, regular_user, monkeypatch
    ):
        profile = await _profile(
            db,
            org,
            regular_user,
            control_mode=StudyLearnerControlMode.DEPENDENT.value,
        )
        _patch_approved_context(monkeypatch, patch_authority=False)
        request = StudyConsentEventCreate(
            purpose="marketing",
            choice="decline",
            notice_version="notice-2026-1",
            scope_codes=["promotion"],
        )
        with pytest.raises(HTTPException) as denied:
            await consents.record_my_consent_choice(
                db,
                org,
                _public_user(regular_user),
                profile.learner_uuid,
                request,
                "consent-dependent-denied",
            )
        assert denied.value.status_code == 403
        assert denied.value.detail["code"] == "CONSENT_ACTOR_NOT_AUTHORIZED"
        assert await db.scalar(select(func.count(StudyConsentEvent.id))) == 0

    async def test_stale_notice_and_unapproved_scope_are_rejected(
        self, db, org, regular_user, monkeypatch
    ):
        profile = await _profile(db, org, regular_user)
        _patch_approved_context(monkeypatch)
        stale = StudyConsentEventCreate(
            purpose="marketing",
            choice="accept",
            notice_version="notice-2025-1",
            scope_codes=["promotion"],
        )
        with pytest.raises(HTTPException) as stale_notice:
            await consents.record_my_consent_choice(
                db,
                org,
                _public_user(regular_user),
                profile.learner_uuid,
                stale,
                "consent-stale-notice",
            )
        assert stale_notice.value.detail["code"] == "CONSENT_NOTICE_STALE"

        unknown_scope = StudyConsentEventCreate(
            purpose="marketing",
            choice="accept",
            notice_version="notice-2026-1",
            scope_codes=["unapproved_scope"],
        )
        with pytest.raises(HTTPException) as scope_denied:
            await consents.record_my_consent_choice(
                db,
                org,
                _public_user(regular_user),
                profile.learner_uuid,
                unknown_scope,
                "consent-unapproved-scope",
            )
        assert scope_denied.value.detail["code"] == "CONSENT_SCOPE_NOT_APPROVED"
        assert await db.scalar(select(func.count(StudyConsentEvent.id))) == 0

    async def test_notice_version_without_approved_display_text_cannot_record_choice(
        self, db, org, regular_user, monkeypatch
    ):
        profile = await _profile(db, org, regular_user)
        _patch_approved_context(monkeypatch)
        current_policies = consents._current_policies

        async def policies_without_notice_text(*args, **kwargs):
            policies = await current_policies(*args, **kwargs)
            d12 = next(policy for policy in policies if policy.policy_key == "D-12")
            body = dict(d12.body)
            decision_items = dict(body["decision_items"])
            consent = dict(decision_items["consent"]["value"])
            consent.pop("notice_texts")
            decision_items["consent"] = {
                **decision_items["consent"],
                "value": consent,
            }
            body["decision_items"] = decision_items
            d12.body = body
            return policies

        monkeypatch.setattr(consents, "_current_policies", policies_without_notice_text)
        request = StudyConsentEventCreate(
            purpose="marketing",
            choice="accept",
            notice_version="notice-2026-1",
            scope_codes=["promotion"],
        )

        with pytest.raises(HTTPException) as denied:
            await consents.record_my_consent_choice(
                db,
                org,
                _public_user(regular_user),
                profile.learner_uuid,
                request,
                "consent-missing-notice-text",
            )

        assert denied.value.status_code == 409
        assert denied.value.detail["code"] == "CONSENT_NOTICE_UNAVAILABLE"
        assert await db.scalar(select(func.count(StudyConsentEvent.id))) == 0

    async def test_accept_replays_and_withdrawal_appends_without_policy_recheck(
        self, db, org, regular_user, monkeypatch
    ):
        profile = await _profile(db, org, regular_user)
        _patch_approved_context(monkeypatch)
        request = StudyConsentEventCreate(
            purpose="marketing",
            choice="accept",
            notice_version="notice-2026-1",
            scope_codes=["promotion"],
        )
        actor = _public_user(regular_user)
        accepted = await consents.record_my_consent_choice(
            db, org, actor, profile.learner_uuid, request, "consent-accept-key"
        )
        replayed = await consents.record_my_consent_choice(
            db, org, actor, profile.learner_uuid, request, "consent-accept-key"
        )
        assert replayed.event_uuid == accepted.event_uuid
        assert await consents.has_current_consent_scope(
            db, org, profile, StudyConsentPurpose.MARKETING, "promotion"
        )

        # A self-consent acceptance cannot carry into dependent processing.
        profile.control_mode = "dependent"
        assert not await consents.has_current_consent_scope(
            db, org, profile, StudyConsentPurpose.MARKETING, "promotion"
        )
        profile.control_mode = "independent"

        withdrawn = await consents.withdraw_my_consent_choice(
            db,
            org,
            actor,
            profile.learner_uuid,
            accepted.event_uuid,
            "consent-withdraw-key",
        )
        assert withdrawn.choice == StudyConsentChoice.WITHDRAW
        assert withdrawn.withdraws_event_uuid == accepted.event_uuid
        assert withdrawn.notice_version == accepted.notice_version
        assert withdrawn.scope_codes == accepted.scope_codes
        assert not await consents.has_current_consent_scope(
            db, org, profile, StudyConsentPurpose.MARKETING, "promotion"
        )
        assert profile.access_version == 1
        assert await db.scalar(select(func.count(StudyConsentEvent.id))) == 2

        current = await consents.list_my_consent_events(
            db, org, actor, profile.learner_uuid
        )
        assert [event.choice for event in current] == [
            StudyConsentChoice.WITHDRAW,
            StudyConsentChoice.ACCEPT,
        ]
