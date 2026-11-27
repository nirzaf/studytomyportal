"""D-10 fail-closed coverage for every mounted provider-backed AI surface."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from src.core.events.database import get_db_session
from src.db.studytomy.family import StudyLearnerProfile
from src.routers.boards.boards_playground import router as boards_playground_router
from src.router import _require_studytomy_ai_policy, v1_router
from src.services.auth.session import mint_session_tokens
from src.services.studytomy.privacy import (
    protected_studytomy_user_ids,
    require_studytomy_learner_ai_policy,
)


def _include_contexts():
    return [
        route.include_context
        for route in v1_router.routes
        if hasattr(route, "include_context")
    ]


def _dependency_calls(include_context) -> set[object]:
    return {dependency.dependency for dependency in include_context.dependencies}


def test_every_mounted_ai_endpoint_checks_studytomy_learner_policy():
    ai_routers = [
        context for context in _include_contexts() if context.prefix == "/api/v1/ai"
    ]

    assert len(ai_routers) == 9
    assert all(
        _require_studytomy_ai_policy in _dependency_calls(context)
        for context in ai_routers
    )


def test_playground_ai_generation_checks_studytomy_learner_policy():
    generation_routers = [
        context
        for context in _include_contexts()
        if context.prefix == "/api/v1/playgrounds"
        and any(
            getattr(route, "path", "").startswith("/generate")
            for route in context.included_router.routes
        )
    ]

    assert len(generation_routers) == 1
    assert all(
        _require_studytomy_ai_policy in _dependency_calls(context)
        for context in generation_routers
    )


def test_board_playground_ai_routes_check_studytomy_learner_policy():
    contexts = [
        context for context in _include_contexts()
        if context.included_router is boards_playground_router
    ]
    assert len(contexts) == 1
    assert _require_studytomy_ai_policy in _dependency_calls(contexts[0])


async def test_protected_learner_ai_policy_returns_409_until_approved(
    db, org, admin_user, regular_user
):
    assert regular_user.id not in await protected_studytomy_user_ids(db)
    db.add(
        StudyLearnerProfile(
            org_id=org.id,
            user_id=regular_user.id,
            created_by_user_id=admin_user.id,
        )
    )
    await db.commit()

    assert await require_studytomy_learner_ai_policy(db, admin_user) is None

    with pytest.raises(HTTPException) as error:
        await require_studytomy_learner_ai_policy(db, regular_user)

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "POLICY_NOT_APPROVED"


async def test_mounted_legacy_ai_rejects_managed_principal_before_handler(
    db, org, admin_user, regular_user
):
    db.add(
        StudyLearnerProfile(
            org_id=org.id, user_id=regular_user.id, created_by_user_id=admin_user.id
        )
    )
    await db.commit()
    app = FastAPI()
    app.include_router(v1_router)
    app.dependency_overrides[get_db_session] = lambda: db
    token = mint_session_tokens(
        regular_user.email, amr="password", org_id=org.id
    ).access_token
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        with patch(
            "src.routers.ai.ai.ai_start_activity_chat_session_stream",
            new_callable=AsyncMock,
        ) as start:
            payload = {"activity_uuid": "activity_fixture", "message": "fixture"}
            for path, body in (
                ("start/activity_chat_session", payload),
                ("send/activity_chat_message", payload | {"aichat_uuid": "old_chat"}),
                ("stream/start/activity_chat_session", payload),
                (
                    "stream/send/activity_chat_message",
                    payload | {"aichat_uuid": "old_chat"},
                ),
                ("rag/search", {"query": "fixture"}),
            ):
                result = await client.post(
                    f"/api/v1/ai/{path}", json=body, headers=headers
                )
                assert result.status_code == 409, (path, result.text)
                assert result.json()["detail"]["code"] == "POLICY_NOT_APPROVED"
            start.assert_not_awaited()
