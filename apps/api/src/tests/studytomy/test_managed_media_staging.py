import asyncio
from hashlib import sha256
from tempfile import TemporaryFile
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import ClientDisconnect, Request

from src.routers.studytomy import commerce
from src.services.studytomy import access_policy


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["cancelled", "timeout", "oversized", "truncated"])
async def test_release_media_staging_closes_temporary_file_on_failure(monkeypatch, failure):
    original_temporary_file = access_policy.TemporaryFile
    staged_files = []
    started = asyncio.Event()

    def track_temporary_file(*args, **kwargs):
        staged_file = original_temporary_file(*args, **kwargs)
        staged_files.append(staged_file)
        return staged_file

    async def open_synthetic_media(**kwargs):
        async def chunks():
            started.set()
            if failure == "oversized":
                yield b"oversized"
                return
            if failure == "truncated":
                yield b""
                return
            await asyncio.Event().wait()
            yield b"x"

        return chunks()

    monkeypatch.setattr(access_policy, "TemporaryFile", track_temporary_file)
    monkeypatch.setattr(access_policy, "open_content_stream", open_synthetic_media)
    if failure == "timeout":
        monkeypatch.setattr(access_policy, "_RELEASE_MEDIA_STAGE_TIMEOUT_SECONDS", 0.01)

    media = {
        "storage_reference": "studytomy/releases/synthetic.bin",
        "size_bytes": 1,
        "sha256": sha256(b"x").hexdigest(),
    }
    staging = asyncio.create_task(access_policy._stage_release_media("org_synthetic", media))
    await asyncio.wait_for(started.wait(), timeout=1)

    if failure == "cancelled":
        staging.cancel()
        with pytest.raises(asyncio.CancelledError):
            await staging
    else:
        with pytest.raises(HTTPException) as rejected:
            await staging
        assert rejected.value.status_code == 503
        assert rejected.value.detail["code"] == (
            "RELEASE_STORAGE_TIMEOUT" if failure == "timeout" else "RELEASE_STORAGE_INVALID"
        )

    assert len(staged_files) == 1
    assert staged_files[0].closed


@pytest.mark.asyncio
async def test_media_response_closes_staged_file_if_commit_is_cancelled(monkeypatch):
    with TemporaryFile() as staged:
        media = access_policy.ManagedActivityMedia(
            file=staged,
            media_type="application/pdf",
            filename="lesson.pdf",
            size_bytes=1,
            start=0,
            end=0,
            status_code=200,
        )
        monkeypatch.setattr(commerce, "_member_org", AsyncMock())
        monkeypatch.setattr(
            commerce, "read_managed_activity_media", AsyncMock(return_value=media)
        )
        db = AsyncMock()
        db.commit.side_effect = asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await commerce._managed_activity_media_response(
                "org_test", "course_test", "release_test", "activity_test",
                Request({"type": "http", "headers": []}), object(), db,
                start_first_use=False,
            )

        assert staged.closed


@pytest.mark.asyncio
async def test_media_response_closes_file_on_header_send_failure():
    with TemporaryFile() as staged:
        media = access_policy.ManagedActivityMedia(
            file=staged, media_type="application/pdf", filename="lesson.pdf",
            size_bytes=1, start=0, end=0, status_code=200,
        )
        response = commerce._ManagedMediaResponse(media, {})
        send = AsyncMock(side_effect=OSError("client disconnected"))

        with pytest.raises(ClientDisconnect):
            await response(
                {"type": "http", "asgi": {"spec_version": "2.4"}},
                AsyncMock(), send,
            )

        assert staged.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("start,status", [(0, 200), (1024, 206)])
async def test_media_stream_stops_before_releasing_next_chunk_after_denial(start, status):
    with TemporaryFile() as staged:
        staged.write(b"x" * (140 * 1024))
        revalidate = AsyncMock(side_effect=[None, HTTPException(status_code=404)])
        media = access_policy.ManagedActivityMedia(
            file=staged, media_type="application/pdf", filename="lesson.pdf",
            size_bytes=140 * 1024, start=start, end=140 * 1024 - 1,
            status_code=status, revalidate=revalidate,
        )
        chunks = [chunk async for chunk in media.stream()]
        assert [len(chunk) for chunk in chunks] == [64 * 1024]
        assert revalidate.await_count == 2
        assert staged.closed
