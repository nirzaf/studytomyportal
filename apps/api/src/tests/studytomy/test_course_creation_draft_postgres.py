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


async def test_atomic_revision_delete_cannot_wipe_concurrent_update(postgres_test_rows):
    """A revision-checked DELETE racing an UPDATE on revision 1 must either
    delete revision 1 before the update, or fail with 409 and preserve revision 2."""
    rows = postgres_test_rows
    try:
        assert (await _attempt(rows.session_factory, rows.org_id, rows.user_id, None, "v1"))[0] == "ok"

        async def _atomic_delete():
            async with rows.session_factory() as session:
                existing = await svc._fetch(session, rows.org_id, rows.user_id)
                assert existing is not None
                stmt = (
                    delete(CourseCreationDraft)
                    .where(
                        CourseCreationDraft.id == existing.id,  # type: ignore[arg-type]
                        CourseCreationDraft.revision == 1,  # type: ignore[arg-type]
                    )
                )
                result = await session.execute(stmt)
                if result.rowcount != 1:  # type: ignore[attr-defined]
                    await session.rollback()
                    return ("conflict", None)
                await session.commit()
                return ("deleted", None)

        update_outcome, delete_outcome = await asyncio.gather(
            _attempt(rows.session_factory, rows.org_id, rows.user_id, 1, "v2-newer"),
            _atomic_delete(),
        )
        # Exactly one of update or delete can succeed; never both.
        assert {update_outcome[0], delete_outcome[0]} in ({"ok", "conflict"}, {"conflict", "deleted"})
        async with rows.session_factory() as session:
            remaining = await svc._fetch(session, rows.org_id, rows.user_id)
            if update_outcome[0] == "ok":
                assert remaining is not None and remaining.name == "v2-newer" and remaining.revision == 2
            else:
                assert remaining is None
    finally:
        async with rows.session_factory() as session:
            await session.execute(delete(CourseCreationDraft).where(CourseCreationDraft.org_id == rows.org_id))
            await session.commit()
