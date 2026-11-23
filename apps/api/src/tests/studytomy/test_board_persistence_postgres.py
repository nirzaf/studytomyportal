import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.core.events.database import get_db_session
from src.db.boards import Board
from src.router import v1_router


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
    app.include_router(v1_router)
    app.dependency_overrides[get_db_session] = test_session
    monkeypatch.setenv("COLLAB_INTERNAL_KEY", fixture.token)
    headers = {"X-Internal-Key": fixture.token}
    path = f"/api/v1/boards/{board_uuid}/ydoc"
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
            f"/api/v1/boards/{board_uuid}_missing/ydoc", headers=headers
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
        rejected = await client.put(path, content=b"", headers=headers)
        assert rejected.status_code == 422

        reread = await client.get(path, headers=headers)
        assert reread.status_code == 200
        assert reread.headers["x-ydoc-state"] == "present"
        assert reread.content == update

        assert reread.headers["etag"] != empty.headers["etag"]
        stale = await client.put(
            path, content=b"stale", headers={**headers, "If-Match": empty.headers["etag"]}
        )
        assert stale.status_code == 412
        assert (await client.get(path, headers=headers)).content == update

        contenders = (b"snapshot one", b"snapshot two")
        conditional_headers = {**headers, "If-Match": reread.headers["etag"]}
        responses = await asyncio.gather(
            *(
                client.put(path, content=state, headers=conditional_headers)
                for state in contenders
            )
        )
        assert sorted(response.status_code for response in responses) == [200, 412]
        winner = next(
            state for state, response in zip(contenders, responses)
            if response.status_code == 200
        )
        latest = await client.get(path, headers=headers)
        assert latest.content == winner
        assert latest.headers["etag"] != reread.headers["etag"]
