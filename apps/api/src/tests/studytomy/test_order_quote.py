from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text

from src.db.studytomy.commerce import StudyOffer, StudyOfferTerms, StudyOfferVersion
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import StudyPolicyKey
from src.db.studytomy.orders import StudyOrder, StudyOrderLine
from src.services.studytomy import orders
from src.services.studytomy.offers import _terms_snapshot
from src.tests.studytomy.test_studytomy_access import _seed_approved_policy


async def test_purchase_authority_is_bound_to_actor_and_learner(db, org, admin_user, regular_user, monkeypatch):
    profile = StudyLearnerProfile(
        org_id=org.id, user_id=regular_user.id, created_by_user_id=admin_user.id,
        status="active", control_mode="dependent",
    )
    db.add(profile)
    await db.commit()
    now = datetime.now(UTC)
    scope = {"learner_profile_id": profile.id, "now": now}
    guardian = AsyncMock(return_value=False)
    monkeypatch.setattr(orders, "has_active_guardian_scope", guardian)
    latest = AsyncMock(return_value=[SimpleNamespace(eligibility_kind="learning")])
    monkeypatch.setattr(orders, "_latest_decisions", latest)
    monkeypatch.setattr(orders, "_decision_read", AsyncMock(return_value=SimpleNamespace(currently_eligible=True)))

    assert not await orders.can_purchase_for_learner(db, org, purchaser_id=regular_user.id, **scope)
    guardian.assert_not_awaited()
    assert not await orders.can_purchase_for_learner(db, org, purchaser_id=admin_user.id, **scope)
    guardian.assert_not_awaited()

    profile.control_mode = "independent"
    await db.commit()
    assert not await orders.can_purchase_for_learner(db, org, purchaser_id=admin_user.id, **scope)
    assert guardian.await_args.args[4] == "purchase_allocation"
    guardian.return_value = True
    assert await orders.can_purchase_for_learner(db, org, purchaser_id=admin_user.id, **scope)
    assert not await orders.can_purchase_for_learner(db, org, purchaser_id=regular_user.id, **scope)
    latest.return_value.append(SimpleNamespace(eligibility_kind="purchase"))
    assert await orders.can_purchase_for_learner(db, org, purchaser_id=regular_user.id, **scope)


async def test_server_quote_uses_current_immutable_offer_and_exact_approved_price(
    db, org, admin_user, regular_user, monkeypatch,
):
    offer_uuid = str(uuid4())
    terms = StudyOfferTerms.model_validate({"customer_terms": "Synthetic accepted terms", "price": {
        "currency": "AUD", "total_minor": 125,
        "lines": [{"kind": "charge", "amount_minor": 125, "label": "Synthetic course"}],
    }})
    snapshot, digest = _terms_snapshot(terms, "course")
    offer = StudyOffer(
        org_id=org.id, offer_uuid=offer_uuid, offer_kind="course", created_by_user_id=admin_user.id,
    )
    db.add(offer)
    await db.flush()
    version = StudyOfferVersion(
        offer_id=offer.id, version_no=1, terms=snapshot, content_sha256=digest,
        created_by_user_id=admin_user.id,
    )
    db.add(version)
    await db.commit()
    args = {"offer_uuid": offer_uuid, "offer_version_uuid": version.version_uuid, "learner_count": 2}
    with pytest.raises(HTTPException) as absent:
        await orders.calculate_order_quote(db, org, **args)
    assert absent.value.detail["code"] == "POLICY_NOT_APPROVED"

    policy = await _seed_approved_policy(
        db, org, admin_user.id, StudyPolicyKey.D_05,
        specified={"prices": {offer_uuid: snapshot["price"]}},
    )
    monkeypatch.setattr(orders, "_active_policy", AsyncMock(return_value=policy))
    quote = await orders.calculate_order_quote(db, org, **args)
    assert quote.total_minor == 250 and quote.price_per_learner.currency == "AUD"
    assert quote.offer_sha256 == digest

    with pytest.raises(HTTPException) as stale:
        await orders.calculate_order_quote(db, org, **{**args, "offer_version_uuid": str(uuid4())})
    assert stale.value.detail["code"] == "OFFER_VERSION_NOT_CURRENT"

    pricing = policy.body["decision_items"]["prices"]["value"][offer_uuid]
    pricing["currency"] = "USD"
    with pytest.raises(HTTPException) as mismatch:
        await orders.calculate_order_quote(db, org, **args)
    assert mismatch.value.detail["code"] == "OFFER_PRICE_NOT_APPROVED"
    pricing["currency"] = "AUD"

    profiles = [
        StudyLearnerProfile(org_id=org.id, user_id=actor.id, created_by_user_id=admin_user.id)
        for actor in (admin_user, regular_user)
    ]
    db.add_all(profiles)
    await db.commit()
    authority = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(orders, "can_purchase_for_learner", authority)
    named = {
        "offer_uuid": offer_uuid, "offer_version_uuid": version.version_uuid,
        "purchaser_id": admin_user.id, "learner_uuids": [profile.learner_uuid for profile in profiles],
    }
    with pytest.raises(HTTPException) as denied:
        await orders.preview_named_order(db, org, **named)
    assert denied.value.detail["code"] == "ORDER_LEARNER_UNAVAILABLE"
    authority.side_effect = None
    authority.return_value = True
    with pytest.raises(HTTPException) as missing_terms:
        await orders.preview_named_order(db, org, **named)
    assert missing_terms.value.detail["code"] == "OFFER_CUSTOMER_TERMS_NOT_APPROVED"
    customer_policy = await _seed_approved_policy(
        db, org, admin_user.id, StudyPolicyKey.D_18,
        specified={"customer_terms": {offer_uuid: terms.customer_terms}},
    )
    async def resolve_policy(db, org, key, now):
        return {StudyPolicyKey.D_05: policy, StudyPolicyKey.D_18: customer_policy}[key]

    monkeypatch.setattr(orders, "_active_policy", resolve_policy)
    preview = await orders.preview_named_order(db, org, **named)
    assert preview.total_minor == 250
    assert preview.learner_uuids == named["learner_uuids"] and preview.learners_with_overlap == []
    assert preview.customer_terms == terms.customer_terms
    confirmation = {
        **named, "confirmation_sha256": preview.confirmation_sha256,
        "acknowledged_overlap": False,
    }
    assert await orders.verify_named_order_confirmation(db, org, **confirmation) == preview
    with pytest.raises(HTTPException) as changed:
        await orders.verify_named_order_confirmation(
            db, org, **{**confirmation, "learner_uuids": list(reversed(named["learner_uuids"]))},
        )
    assert changed.value.detail["code"] == "ORDER_QUOTE_CHANGED"
    await db.execute(text("BEGIN"))
    reference = await orders.record_named_order_draft(db, org, **confirmation, idempotency_key="synthetic-draft")
    await db.commit()
    assert await orders.record_named_order_draft(
        db, org, **confirmation, idempotency_key="synthetic-draft",
    ) == reference
    with pytest.raises(HTTPException) as conflict:
        await orders.record_named_order_draft(
            db, org, **{**confirmation, "acknowledged_overlap": True},
            idempotency_key="synthetic-draft",
        )
    assert conflict.value.detail["code"] == "IDEMPOTENCY_CONFLICT"
    saved = (await db.execute(select(StudyOrder))).scalar_one()
    assert saved.customer_ref == reference and saved.total_minor == 250 and saved.currency == "AUD"
    lines = (await db.execute(select(StudyOrderLine).order_by(StudyOrderLine.id))).scalars().all()
    assert [line.learner_profile_id for line in lines] == [profile.id for profile in profiles]
    assert all(line.total_minor == 125 and line.terms_snapshot == snapshot for line in lines)
    pilot = await _seed_approved_policy(
        db, org, admin_user.id, StudyPolicyKey.D_17,
        specified={"pilot_size": {"order_acceptance_offer_versions": {offer_uuid: "other-version"}}},
    )
    monkeypatch.setattr(orders, "_current_policies", AsyncMock(return_value=[policy, customer_policy, pilot]))
    with pytest.raises(HTTPException) as closed:
        await orders.accept_named_order(db, org, **confirmation, customer_ref=reference)
    assert closed.value.detail["code"] == "OFFER_SALES_PAUSED"
    pilot.body["decision_items"]["pilot_size"]["value"]["order_acceptance_offer_versions"][offer_uuid] = version.version_uuid
    with pytest.raises(HTTPException) as incomplete:
        await orders.accept_named_order(db, org, **confirmation, customer_ref=reference)
    assert incomplete.value.detail["code"] == "OFFER_TERMS_INCOMPLETE"
    monkeypatch.setattr(StudyOfferTerms, "completion_blockers", lambda self: [])
    monkeypatch.setattr(orders, "_snapshot_resource_titles", AsyncMock(return_value=terms))
    accepted = await orders.accept_named_order(db, org, **confirmation, customer_ref=reference)
    assert accepted.order_id == saved.id and accepted.confirmation_sha256 == preview.confirmation_sha256
    assert accepted.policy_binding["D-17"]["uuid"] == pilot.policy_uuid
    await db.commit()
    assert await orders.accept_named_order(db, org, **confirmation, customer_ref=reference) == accepted

    overlap = preview.model_copy(update={"learners_with_overlap": named["learner_uuids"][:1]})
    monkeypatch.setattr(orders, "preview_named_order", AsyncMock(return_value=overlap))
    with pytest.raises(HTTPException) as unacknowledged:
        await orders.verify_named_order_confirmation(db, org, **confirmation)
    assert unacknowledged.value.detail["code"] == "ORDER_OVERLAP_ACKNOWLEDGEMENT_REQUIRED"
