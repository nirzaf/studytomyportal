from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from src.core.events.database import get_db_session
from src.db.studytomy.commerce import StudyOffer, StudyOfferTerms, StudyOfferVersion
from src.db.studytomy.governance import StudyOwnerFunction, StudyPolicyKey
from src.routers.studytomy.offers import router
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.security.studytomy_access import StudytomyCapabilityName
from src.services.studytomy import capacity
from src.services.studytomy.offers import _terms_snapshot
from src.tests.studytomy.test_studytomy_access import (
    _grant_role_capabilities,
    _seed_active_owner,
    _seed_approved_policy,
    _seed_staff_scope,
)


async def test_offer_readiness_lists_missing_approvals_for_assigned_staff(
    db, org, admin_user, regular_user,
):
    offer_uuid = str(uuid4())
    org_uuid = org.org_uuid
    await _grant_role_capabilities(db, org.id, admin_user.id, offer_publication=True)
    assignment = await _seed_active_owner(
        db, org, admin_user.id, regular_user.id, StudyOwnerFunction.BUSINESS,
    )
    await _seed_staff_scope(
        db, org.id, assignment.id, StudytomyCapabilityName.OFFER_PUBLICATION,
        "offer", offer_uuid, regular_user.id,
    )
    await _seed_approved_policy(db, org, regular_user.id, StudyPolicyKey.D_02)
    await db.commit()
    app = FastAPI()
    app.include_router(router)

    async def session():
        try:
            await db.execute(text("BEGIN"))
            yield db
        finally:
            await db.rollback()

    app.dependency_overrides[get_db_session] = session
    app.dependency_overrides[get_authenticated_non_api_token_user] = lambda: admin_user
    root = f"/orgs/{org_uuid}/offers"
    url = f"{root}/{offer_uuid}/draft/readiness"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(
            f"{root}/drafts",
            json={"offer_uuid": offer_uuid, "offer_kind": "course", "terms": {}},
            headers={"Idempotency-Key": "synthetic-offer"},
        )
        assert created.status_code == 201, created.text
        response = await client.get(url)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        payload = response.json()
        assert payload["version_uuid"] == created.json()["current_version_uuid"]
        assert payload["validation_blockers"] == created.json()["validation_blockers"]
        assert payload["policy_blockers"] == [
            {"policy_key": "D-02", "code": "POLICY_APPROVAL_EVIDENCE_MISSING"},
            {"policy_key": "D-05", "code": "POLICY_NOT_APPROVED"},
            {"policy_key": "D-18", "code": "POLICY_NOT_APPROVED"},
            {"policy_key": "D-20", "code": "POLICY_NOT_APPROVED"},
        ]
        offer = (await db.execute(select(StudyOffer).where(
            StudyOffer.offer_uuid == offer_uuid,
        ))).scalar_one()
        stale_terms = StudyOfferTerms.model_validate({"capacity_is_limited": True, "capacity_limit": 2, "resources": [{
            "kind": "course", "resource_uuid": "course_missing",
            "title_snapshot": "Historical title", "release_uuid": "course_release_missing",
            "release_sha256": "a" * 64,
        }]})
        terms, digest = _terms_snapshot(stale_terms, "course")
        version = StudyOfferVersion(
            offer_id=offer.id, version_no=2, terms=terms, content_sha256=digest,
            created_by_user_id=admin_user.id,
        )
        db.add(version)
        await db.flush()
        version_uuid = version.version_uuid
        offer.current_version_no = 2
        await db.commit()
        stale = await client.get(url)
        assert stale.status_code == 200, stale.text
        assert stale.json()["version_uuid"] == version_uuid
        assert "OFFER_COURSE_RELEASE_UNAVAILABLE" in stale.json()["validation_blockers"]
        assert "OFFER_CAPACITY_POLICY_UNAVAILABLE" in stale.json()["validation_blockers"]
        historical = await client.get(f"{root}/{offer_uuid}/draft", params={"version_no": 2})
        assert historical.json()["terms"]["resources"][0]["release_uuid"] == "course_release_missing"
        app.dependency_overrides[get_authenticated_non_api_token_user] = lambda: regular_user
        denied = await client.get(url)
        assert denied.status_code == 403
        assert "policy_blockers" not in denied.json()


async def test_capacity_terms_require_explicit_matching_decisions(db, org, admin_user, monkeypatch):
    offer_uuid = str(uuid4())
    policies = [
        await _seed_approved_policy(db, org, admin_user.id, key, specified={area: {field: {offer_uuid: value}}})
        for key, area, field, value in (
            (StudyPolicyKey.D_04, "staffing", "offer_place_limits", 4),
            (StudyPolicyKey.D_05, "learner_allocation", "offer_hold_minutes", 7),
            (StudyPolicyKey.D_17, "pilot_size", "offer_place_limits", 3),
        )
    ]
    resolver = AsyncMock(return_value=policies)
    monkeypatch.setattr(capacity, "_current_policies", resolver)
    now = datetime.now(UTC)
    terms = StudyOfferTerms(capacity_is_limited=True, capacity_limit=3)
    assert await capacity.resolve_finite_capacity_terms(db, org, offer_uuid, terms, now) == (
        3, timedelta(minutes=7),
    )
    terms.capacity_limit = 4
    with pytest.raises(HTTPException) as blocked:
        await capacity.resolve_finite_capacity_terms(db, org, offer_uuid, terms, now)
    assert blocked.value.detail["code"] == "OFFER_CAPACITY_POLICY_UNCONFIGURED"
