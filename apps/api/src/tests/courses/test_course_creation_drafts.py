"""Create Course draft persistence: ownership, concurrency, thumbnail, cleanup."""

import base64
import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException, UploadFile
from sqlmodel import select
from starlette.datastructures import Headers

from src.db.courses.course_creation_drafts import CourseCreationDraft, CourseDraftFields
from src.db.courses.courses import Course
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.services.courses import course_drafts as svc

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def _isolated_content_root(monkeypatch, tmp_path):
    from src.services.utils import upload_content as uc

    monkeypatch.setattr(uc, "_CONTENT_ROOT", str(tmp_path))


def _png(name: str = "t.png") -> UploadFile:
    return UploadFile(file=io.BytesIO(PNG), filename=name, headers=Headers({"content-type": "image/png"}))


async def _second_admin(db, org, admin_role) -> PublicUser:
    user = User(
        id=3,
        username="admin2",
        first_name="Second",
        last_name="Admin",
        email="admin2@test.com",
        password="x",
        user_uuid="user_admin2",
        creation_date=str(datetime.now()),
        update_date=str(datetime.now()),
    )
    db.add(user)
    await db.commit()
    db.add(
        UserOrganization(
            user_id=3,
            org_id=org.id,
            role_id=admin_role.id,
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
    )
    await db.commit()
    return PublicUser(
        id=3, username="admin2", first_name="Second", last_name="Admin",
        email="admin2@test.com", user_uuid="user_admin2",
    )


async def test_incomplete_draft_saves_and_resumes_without_creating_a_course(db, org, admin_user):
    assert await svc.get_my_draft(db, org.id, admin_user) is None
    saved = await svc.save_my_draft(db, org.id, admin_user, CourseDraftFields(name="Mom"))
    assert (saved.name, saved.description, saved.revision, saved.has_thumbnail) == ("Mom", "", 1, False)

    again = await svc.get_my_draft(db, org.id, admin_user)
    assert again and again.name == "Mom" and again.public is True
    assert (await db.execute(select(Course))).scalars().all() == []


async def test_stale_write_is_rejected_with_current_draft_and_revision_advances(db, org, admin_user):
    first = await svc.save_my_draft(db, org.id, admin_user, CourseDraftFields(name="A"))
    second = await svc.save_my_draft(
        db, org.id, admin_user,
        CourseDraftFields(name="B", tags="x,y", public=False, base_revision=first.revision),
    )
    assert second.revision == 2 and second.public is False and second.tags == "x,y"

    with pytest.raises(HTTPException) as stale:  # tab 1 still believes revision 1
        await svc.save_my_draft(db, org.id, admin_user, CourseDraftFields(name="C", base_revision=1))
    assert stale.value.status_code == 409
    assert stale.value.detail["code"] == svc.CONFLICT_CODE
    assert stale.value.detail["draft"]["name"] == "B"

    with pytest.raises(HTTPException) as dup_create:  # "no draft yet" while one exists
        await svc.save_my_draft(db, org.id, admin_user, CourseDraftFields(name="D"))
    assert dup_create.value.status_code == 409


async def test_drafts_are_isolated_per_user_and_organization(db, org, other_org, admin_user, admin_role):
    other_admin = await _second_admin(db, org, admin_role)
    await svc.save_my_draft(db, org.id, admin_user, CourseDraftFields(name="mine"))

    assert await svc.get_my_draft(db, org.id, other_admin) is None
    with pytest.raises(HTTPException) as no_thumb:
        await svc.get_my_draft_thumbnail(db, org.id, other_admin)
    assert no_thumb.value.status_code == 404

    # Same user, organization they do not belong to: refused before any lookup.
    with pytest.raises(HTTPException) as foreign_org:
        await svc.get_my_draft(db, other_org.id, admin_user)
    assert foreign_org.value.status_code == 403
    with pytest.raises(HTTPException) as foreign_write:
        await svc.save_my_draft(db, other_org.id, admin_user, CourseDraftFields(name="x"))
    assert foreign_write.value.status_code == 403


async def test_user_without_course_create_right_cannot_use_drafts(db, org, regular_user):
    with pytest.raises(HTTPException) as denied:
        await svc.save_my_draft(db, org.id, regular_user, CourseDraftFields(name="x"))
    assert denied.value.status_code == 403
    assert (await db.execute(select(CourseCreationDraft))).scalars().all() == []


async def test_thumbnail_round_trip_replace_clear_and_stale_rejection(db, org, admin_user):
    created = await svc.save_my_draft(db, org.id, admin_user, CourseDraftFields(name="T"))
    with_thumb = await svc.set_my_draft_thumbnail(db, org.id, admin_user, _png(), created.revision)
    assert with_thumb.has_thumbnail and with_thumb.thumbnail_content_type == "image/png"
    assert with_thumb.revision == created.revision + 1

    data, content_type = await svc.get_my_draft_thumbnail(db, org.id, admin_user)
    assert data == PNG and content_type == "image/png"

    with pytest.raises(HTTPException) as stale:
        await svc.set_my_draft_thumbnail(db, org.id, admin_user, _png(), created.revision)
    assert stale.value.status_code == 409

    cleared = await svc.clear_my_draft_thumbnail(db, org.id, admin_user, with_thumb.revision)
    assert not cleared.has_thumbnail
    with pytest.raises(HTTPException) as gone:
        await svc.get_my_draft_thumbnail(db, org.id, admin_user)
    assert gone.value.status_code == 404


async def test_thumbnail_can_create_the_draft_and_invalid_files_are_rejected(db, org, admin_user):
    made = await svc.set_my_draft_thumbnail(db, org.id, admin_user, _png(), None)
    assert made.revision == 1 and made.has_thumbnail

    bad = UploadFile(
        file=io.BytesIO(b"<html><script>alert(1)</script></html>"),
        filename="x.png",
        headers=Headers({"content-type": "image/png"}),
    )
    with pytest.raises(HTTPException) as rejected:
        await svc.set_my_draft_thumbnail(db, org.id, admin_user, bad, made.revision)
    assert rejected.value.status_code in (400, 415, 422)
    assert (await svc.get_my_draft(db, org.id, admin_user)).revision == made.revision  # type: ignore[union-attr]


async def test_delete_honours_revision_and_is_idempotent(db, org, admin_user):
    draft = await svc.save_my_draft(db, org.id, admin_user, CourseDraftFields(name="D"))
    with pytest.raises(HTTPException) as stale:
        await svc.delete_my_draft(db, org.id, admin_user, draft.revision + 5)
    assert stale.value.status_code == 409
    assert await svc.get_my_draft(db, org.id, admin_user) is not None

    await svc.delete_my_draft(db, org.id, admin_user, None)  # unconditional (after course creation)
    assert await svc.get_my_draft(db, org.id, admin_user) is None
    await svc.delete_my_draft(db, org.id, admin_user, None)  # already gone: no error


async def test_expired_draft_is_purged_on_read(db, org, admin_user, monkeypatch, tmp_path):
    from src.services.utils import upload_content as uc

    monkeypatch.setattr(uc, "_CONTENT_ROOT", str(tmp_path))
    created = await svc.save_my_draft(db, org.id, admin_user, CourseDraftFields(name="old"))
    await svc.set_my_draft_thumbnail(db, org.id, admin_user, _png(), created.revision)
    row = (await db.execute(select(CourseCreationDraft))).scalars().one()
    stored_file = tmp_path / "orgs" / org.org_uuid / "studytomy" / "course-drafts" / row.draft_uuid / row.thumbnail_filename
    assert stored_file.exists()

    row.updated_at = datetime.now(timezone.utc) - svc.DRAFT_TTL - timedelta(days=1)
    db.add(row)
    await db.commit()

    assert await svc.get_my_draft(db, org.id, admin_user) is None
    assert (await db.execute(select(CourseCreationDraft))).scalars().all() == []
    assert not stored_file.exists()


async def test_private_storage_path_is_blocked_from_generic_content_router(db, org, admin_user, monkeypatch, tmp_path):
    from src.security.submission_file_access import is_private_studytomy_path, split_content_path
    from src.services.utils import upload_content as uc

    monkeypatch.setattr(uc, "_CONTENT_ROOT", str(tmp_path))
    created = await svc.set_my_draft_thumbnail(db, org.id, admin_user, _png(), None)
    row = (await db.execute(select(CourseCreationDraft))).scalars().one()
    rel_path = f"orgs/{org.org_uuid}/studytomy/course-drafts/{created.draft_uuid}/{row.thumbnail_filename}"
    assert is_private_studytomy_path(split_content_path(rel_path)) is True

    await svc.delete_my_draft(db, org.id, admin_user, created.revision)
    assert not (tmp_path / rel_path).exists()


async def test_duplicate_course_creation_for_same_draft_is_idempotent(db, org, admin_user, admin_role):
    from unittest.mock import AsyncMock, MagicMock, patch
    from src.db.courses.courses import CourseCreate
    from src.services.courses.courses import create_course

    draft = await svc.save_my_draft(db, org.id, admin_user, CourseDraftFields(name="Physics", description="Mechanics"))
    other_admin = await _second_admin(db, org, admin_role)
    req = MagicMock()
    payload = CourseCreate(
        name="Physics",
        description="Mechanics",
        org_id=org.id,
        public=True,
        thumbnail_image="",
        thumbnail_video="",
        about="Mechanics",
        open_to_contributors=False,
    )

    with (
        patch("src.services.courses.courses.check_limits_with_usage", new_callable=AsyncMock),
        patch("src.services.courses.courses.increase_feature_usage", new_callable=AsyncMock),
        patch("src.services.courses.courses.index_course"),
        patch("src.services.courses.courses.track", new_callable=AsyncMock),
    ):
        first = await create_course(req, org.id, payload, admin_user, db, creation_draft_uuid=draft.draft_uuid)
        second = await create_course(req, org.id, payload, admin_user, db, creation_draft_uuid=draft.draft_uuid)
        assert first.course_uuid == second.course_uuid
        assert len((await db.execute(select(Course))).scalars().all()) == 1

        with pytest.raises(HTTPException) as stolen:
            await create_course(req, org.id, payload, other_admin, db, creation_draft_uuid=draft.draft_uuid)
        assert stolen.value.status_code == 409
