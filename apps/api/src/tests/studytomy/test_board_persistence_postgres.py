import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.events.database import get_db_session
from src.db.boards import Board
from src.routers.boards.boards import internal_router


@pytest.mark.asyncio
@pytest.mark.studytomy_postgres
async def test_board_storage_distinguishes_empty_missing_and_persisted_state(
    postgres_test_rows, monkeypatch
):
    fixture = postgres_test_rows
    board_uuid = f"board_{fixture.token}"
    async with fixture.session_factory() as session:
        session.add(
            Board(
                name="Synthetic recovery board",
                board_uuid=board_uuid,
                org_id=fixture.org_id,
                created_by=fixture.user_id,
            )
        )
        await session.commit()

    async def test_session():
        async with fixture.session_factory() as session:
            yield session

    app = FastAPI()
    app.include_router(internal_router, prefix="/api/v1/internal/boards")
    app.dependency_overrides[get_db_session] = test_session
    monkeypatch.setenv("COLLAB_INTERNAL_KEY", fixture.token)
    headers = {"X-Internal-Key": fixture.token}
    path = f"/api/v1/internal/boards/{board_uuid}/ydoc"
    # The storage API retains an opaque Yjs update.
    update = b"\x00\x00"
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        empty = await client.get(path, headers=headers)
        assert empty.status_code == 200
        assert empty.headers["x-ydoc-state"] == "empty"
        assert empty.content == b""

        missing = await client.get(
            f"/api/v1/internal/boards/{board_uuid}_missing/ydoc", headers=headers
        )
        assert missing.status_code == 404
        assert "x-ydoc-state" not in missing.headers

        denied = await client.put(
            path, content=b"unauthorized", headers={"X-Internal-Key": "wrong"}
        )
        assert denied.status_code == 403
        assert (await client.get(path, headers=headers)).headers["x-ydoc-state"] == "empty"

        stored = await client.put(path, content=update, headers=headers)
        assert stored.status_code == 200
        reread = await client.get(path, headers=headers)
        assert reread.status_code == 200
        assert reread.headers["x-ydoc-state"] == "present"
        assert reread.content == update
