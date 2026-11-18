"""get_current_user publishes session provenance on every auth path.

These call get_current_user directly (rather than relying on a whole request
flow) so each provenance branch (JWT session, org API token, superadmin API
token) is covered deterministically, independent of suite ordering.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException
from sqlmodel import select

from src.db.users import User
from src.security.auth import JWT_COOKIE_NAME, get_current_user
from src.security.session_context import (
    AUTH_METHOD_API_TOKEN,
    get_session_provenance,
    set_session_provenance,
)
from src.services.auth.session import advance_session_generation, mint_session_tokens


@pytest.fixture(autouse=True)
def _clear():
    set_session_provenance(None)
    yield
    set_session_provenance(None)


def _request(*, auth: str = "", cookies: dict | None = None) -> Mock:
    req = Mock()
    req.headers = {"Authorization": auth}
    req.cookies = cookies or {}
    req.state = SimpleNamespace()
    return req


@pytest.mark.asyncio
class TestProvenancePublished:
    async def test_jwt_session_publishes_amr_and_sorg(self, db, regular_user):
        user = (
            await db.execute(select(User).where(User.id == regular_user.id))
        ).scalars().first()
        tokens = mint_session_tokens(user.email, amr="password", org_id=55)
        req = _request(cookies={JWT_COOKIE_NAME: tokens.access_token})

        with patch("src.security.auth._record_activity_from_request"):
            result = await get_current_user(req, db)

        assert result.email == user.email
        prov = get_session_provenance()
        assert prov is not None
        assert prov.amr == "password"
        assert prov.org_id == 55

    async def test_session_generation_is_durable_when_redis_is_unavailable(
        self, db, regular_user
    ):
        user = (
            await db.execute(select(User).where(User.id == regular_user.id))
        ).scalars().one()
        old_tokens = mint_session_tokens(
            user.email, session_generation=user.session_generation
        )
        new_generation = await advance_session_generation(db, user.id)

        stale_request = _request(cookies={JWT_COOKIE_NAME: old_tokens.access_token})
        with patch("src.security.auth._get_revocation_redis_client", return_value=None), \
             patch("src.security.auth._record_activity_from_request"):
            with pytest.raises(HTTPException) as rejected:
                await get_current_user(stale_request, db)
        assert rejected.value.status_code == 401

        current_tokens = mint_session_tokens(
            user.email, session_generation=new_generation
        )
        current_request = _request(cookies={JWT_COOKIE_NAME: current_tokens.access_token})
        with patch("src.security.auth._get_revocation_redis_client", return_value=None), \
             patch("src.security.auth._record_activity_from_request"):
            accepted = await get_current_user(current_request, db)
        assert accepted.email == user.email

    async def test_org_api_token_publishes_api_token_and_org(self, db):
        token_user = SimpleNamespace(id=0, org_id=77, created_by_user_id=3)
        req = _request(auth="Bearer lh_faketoken")

        with patch("src.security.auth.validate_api_token", return_value=token_user), patch(
            "src.security.auth._verify_api_token_org_boundary"
        ):
            result = await get_current_user(req, db)

        assert result is token_user
        prov = get_session_provenance()
        assert prov.amr == AUTH_METHOD_API_TOKEN
        assert prov.org_id == 77

    async def test_superadmin_api_token_publishes_api_token_no_org(self, db):
        sa_user = SimpleNamespace(id=0, is_superadmin=True)
        req = _request(auth="Bearer lh_sa_faketoken")

        # Superadmin tokens are an EE credential and are refused outright in
        # 'oss', which the suite pins globally. Run this against EE.
        with patch(
            "src.core.deployment_mode.get_deployment_mode", return_value="ee"
        ), patch("src.security.auth.validate_superadmin_api_token", return_value=sa_user):
            result = await get_current_user(req, db)

        assert result is sa_user
        prov = get_session_provenance()
        assert prov.amr == AUTH_METHOD_API_TOKEN
        assert prov.org_id is None
