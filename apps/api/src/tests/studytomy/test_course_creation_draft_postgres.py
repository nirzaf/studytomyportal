"""PostgreSQL-only: concurrent Create Course draft writes are compare-and-swap safe.

Opt in with STUDYTOMY_TEST_DATABASE_URL (see conftest.py); skipped otherwise.
"""

import asyncio

from fastapi import HTTPException
from sqlalchemy import delete

from src.db.courses.course_creation_drafts import CourseCreationDraft
from src.services.courses import course_drafts as svc


async def _attempt(factory, org_id, user_id, base, name):
    async with factory() as session:
        try:
            result = await svc._apply(session, org_id, user_id, base, {"name": name})
            return ("ok", result.revision)
        except HTTPException as error:
            assert error.status_code == 409
            return ("conflict", None)


async def test_concurrent_first_creates_yield_one_draft(postgres_test_rows):
    rows = postgres_test_rows
    try:
        outcomes = await asyncio.gather(
            *[_attempt(rows.session_factory, rows.org_id, rows.user_id, None, f"tab-{i}") for i in range(8)]
        )
        assert [o[0] for o in outcomes].count("ok") == 1
        assert [o[0] for o in outcomes].count("conflict") == 7
    finally:
        async with rows.session_factory() as session:
            await session.execute(delete(CourseCreationDraft).where(CourseCreationDraft.org_id == rows.org_id))
            await session.commit()


async def test_concurrent_updates_from_same_revision_yield_one_winner(postgres_test_rows):
    rows = postgres_test_rows
    try:
        assert (await _attempt(rows.session_factory, rows.org_id, rows.user_id, None, "base"))[0] == "ok"
        outcomes = await asyncio.gather(
            *[_attempt(rows.session_factory, rows.org_id, rows.user_id, 1, f"tab-{i}") for i in range(6)]
        )
        assert [o[0] for o in outcomes].count("ok") == 1
        async with rows.session_factory() as session:
            current = await svc._fetch(session, rows.org_id, rows.user_id)
            assert current is not None and current.revision == 2
    finally:
        async with rows.session_factory() as session:
            await session.execute(delete(CourseCreationDraft).where(CourseCreationDraft.org_id == rows.org_id))
            await session.commit()
