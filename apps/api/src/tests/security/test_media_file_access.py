"""
Security tests for media-file access (folder-aware) + random share links.

Targets the access *decisions* (the security-critical part) via the service
authorizers, not the byte streaming.
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlmodel import select

from src.db.courses.activities import Activity, ActivitySubTypeEnum, ActivityTypeEnum
from src.db.courses.courses import Course
from src.db.folders.folders import Folder
from src.db.folders.folder_content import FolderContent
from src.db.media.media import Media, MediaTypeEnum
from src.db.media.media_share_token import MediaShareToken
from src.services.media.media import (
    authorize_media_file,
    authorize_share_token,
    create_media_share_link,
)


async def _mk_media(db, org, public=True, name="m"):
    m = Media(
        name=name, media_type=MediaTypeEnum.UPLOAD, public=public, org_id=org.id,
        media_uuid=f"media_{name}", file_id="x.pdf",
        storage_key=f"orgs/{org.org_uuid}/media/rand/x.pdf", file_format="pdf",
        creation_date=str(datetime.now()), update_date=str(datetime.now()),
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


async def _mk_folder(db, org, public, name):
    f = Folder(
        name=name, public=public, org_id=org.id, folder_uuid=f"folder_{name}",
        creation_date=str(datetime.now()), update_date=str(datetime.now()),
    )
    db.add(f)
    await db.commit()
    await db.refresh(f)
    return f


async def _place(db, org, folder_id, media_uuid):
    db.add(FolderContent(
        folder_id=folder_id, resource_uuid=media_uuid, org_id=org.id, position=0,
        creation_date=str(datetime.now()), update_date=str(datetime.now()),
    ))
    await db.commit()


class TestMediaFileAccess:
    @pytest.mark.asyncio
    async def test_public_root_media_allowed_for_anon(self, db, org, anonymous_user, mock_request):
        m = await _mk_media(db, org, public=True, name="rootpub")
        media, is_public = await authorize_media_file(mock_request, m.media_uuid, anonymous_user, db)
        assert media.media_uuid == m.media_uuid
        assert is_public is True

    @pytest.mark.asyncio
    async def test_public_media_in_private_folder_denied_for_anon(self, db, org, anonymous_user, mock_request):
        m = await _mk_media(db, org, public=True, name="inpriv")
        priv = await _mk_folder(db, org, public=False, name="priv")
        await _place(db, org, priv.id, m.media_uuid)
        with pytest.raises(HTTPException) as exc:
            await authorize_media_file(mock_request, m.media_uuid, anonymous_user, db)
        assert exc.value.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_private_folder_media_allowed_for_org_admin(self, db, org, admin_user, mock_request):
        m = await _mk_media(db, org, public=True, name="adminok")
        priv = await _mk_folder(db, org, public=False, name="priv2")
        await _place(db, org, priv.id, m.media_uuid)
        media, is_public = await authorize_media_file(mock_request, m.media_uuid, admin_user, db)
        assert media.media_uuid == m.media_uuid
        assert is_public is False  # folder-private → cache as private

    @pytest.mark.asyncio
    async def test_non_public_media_denied_for_anon(self, db, org, anonymous_user, mock_request):
        m = await _mk_media(db, org, public=False, name="secret")
        with pytest.raises(HTTPException):
            await authorize_media_file(mock_request, m.media_uuid, anonymous_user, db)

    @pytest.mark.asyncio
    async def test_missing_media_404(self, db, anonymous_user, mock_request):
        with pytest.raises(HTTPException) as exc:
            await authorize_media_file(mock_request, "media_nope", anonymous_user, db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_library_media_referenced_by_managed_course_is_not_native_file_accessible(
        self, db, org, admin_user, mock_request,
    ):
        media = await _mk_media(db, org, public=True, name="managed_ref")
        course = Course(
            name="Managed private draft", description="", public=False, published=False,
            open_to_contributors=False, org_id=org.id, course_uuid="course_managed_media",
            studytomy_managed=True, creation_date="", update_date="",
        )
        db.add(course)
        await db.flush()
        db.add(Activity(
            name="Lesson", activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE,
            content={"type": "doc", "content": [{
                "type": "blockLibrary",
                "attrs": {"resourceType": "media", "resourceUuid": media.media_uuid},
            }]},
            published=False, org_id=org.id, course_id=course.id,
            activity_uuid="activity_managed_media", creation_date="", update_date="",
        ))
        await db.commit()

        with pytest.raises(HTTPException) as denied:
            await authorize_media_file(mock_request, media.media_uuid, admin_user, db)
        assert denied.value.status_code == 404

        with pytest.raises(HTTPException) as share_denied:
            await create_media_share_link(mock_request, media.media_uuid, admin_user, db)
        assert share_denied.value.status_code == 404
        tokens = (await db.execute(select(MediaShareToken))).scalars().all()
        assert tokens == []

    @pytest.mark.asyncio
    async def test_unmanaged_course_reference_does_not_disable_standalone_media(
        self, db, org, anonymous_user, mock_request,
    ):
        media = await _mk_media(db, org, public=True, name="native_ref")
        course = Course(
            name="Native", description="", public=True, published=True,
            open_to_contributors=False, org_id=org.id, course_uuid="course_native_media",
            creation_date="", update_date="",
        )
        db.add(course)
        await db.flush()
        db.add(Activity(
            name="Lesson", activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE,
            content={"type": "doc", "content": [{
                "type": "blockLibrary",
                "attrs": {"resourceType": "media", "resourceUuid": media.media_uuid},
            }]},
            published=True, org_id=org.id, course_id=course.id,
            activity_uuid="activity_native_media", creation_date="", update_date="",
        ))
        await db.commit()

        resolved, is_public = await authorize_media_file(
            mock_request, media.media_uuid, anonymous_user, db,
        )
        assert resolved.media_uuid == media.media_uuid
        assert is_public is True

    @pytest.mark.asyncio
    async def test_immutable_managed_release_reference_is_not_native_media_accessible(
        self, db, org, admin_user, mock_request,
    ):
        from src.db.studytomy.education import StudyCourseRelease

        media = await _mk_media(db, org, public=True, name="release_ref")
        course = Course(
            name="Managed", description="", public=False, published=False,
            open_to_contributors=False, org_id=org.id, course_uuid="course_release_media",
            studytomy_managed=True, creation_date="", update_date="",
        )
        db.add(course)
        await db.flush()
        content = {"type": "doc", "content": [{
            "type": "blockLibrary",
            "attrs": {"resourceType": "media", "resourceUuid": media.media_uuid},
        }]}
        snapshot = {
            "course": {"course_uuid": course.course_uuid, "org_id": org.id},
            "activities": [{"content": content}],
        }
        db.add(StudyCourseRelease(
            org_id=org.id, course_uuid=course.course_uuid, review_id=1,
            snapshot_sha256="a" * 64, snapshot=snapshot, media_manifest={},
            assessment_policy_id=1, progression_policy_id=1, retention_policy_id=1,
            rights_policy_id=1, retention_category="course_release",
        ))
        await db.commit()

        with pytest.raises(HTTPException) as denied:
            await authorize_media_file(mock_request, media.media_uuid, admin_user, db)
        assert denied.value.status_code == 404


class TestMediaShareLink:
    @pytest.mark.asyncio
    async def test_share_link_unique_every_call(self, db, org, admin_user, mock_request):
        m = await _mk_media(db, org, public=True, name="share1")
        a = await create_media_share_link(mock_request, m.media_uuid, admin_user, db)
        b = await create_media_share_link(mock_request, m.media_uuid, admin_user, db)
        assert a["token"] and b["token"]
        assert a["token"] != b["token"]

    @pytest.mark.asyncio
    async def test_share_token_resolves_for_owner(self, db, org, admin_user, mock_request):
        m = await _mk_media(db, org, public=True, name="share2")
        tok = (await create_media_share_link(mock_request, m.media_uuid, admin_user, db))["token"]
        media, _ = await authorize_share_token(mock_request, tok, admin_user, db)
        assert media.media_uuid == m.media_uuid

    @pytest.mark.asyncio
    async def test_share_token_not_a_bypass_for_private(self, db, org, admin_user, anonymous_user, mock_request):
        m = await _mk_media(db, org, public=True, name="share3")
        priv = await _mk_folder(db, org, public=False, name="priv3")
        await _place(db, org, priv.id, m.media_uuid)
        tok = (await create_media_share_link(mock_request, m.media_uuid, admin_user, db))["token"]
        # Anonymous recipient with the link is still denied.
        with pytest.raises(HTTPException) as exc:
            await authorize_share_token(mock_request, tok, anonymous_user, db)
        assert exc.value.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_invalid_token_404(self, db, anonymous_user, mock_request):
        with pytest.raises(HTTPException) as exc:
            await authorize_share_token(mock_request, "doesnotexist", anonymous_user, db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_share_link_missing_media_404(self, db, admin_user, mock_request):
        with pytest.raises(HTTPException) as exc:
            await create_media_share_link(mock_request, "media_nope", admin_user, db)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_share_token_to_deleted_media_404(self, db, org, admin_user, mock_request):
        from src.db.media.media_share_token import MediaShareToken
        db.add(MediaShareToken(
            token="danglingtok", media_uuid="media_deleted", org_id=org.id,
            revoked=False, creation_date=str(datetime.now()), update_date=str(datetime.now()),
        ))
        await db.commit()
        with pytest.raises(HTTPException) as exc:
            await authorize_share_token(mock_request, "danglingtok", admin_user, db)
        assert exc.value.status_code == 404


class TestCheckContentAccessMediaBranch:
    """The legacy /content/...media... gate in both serving routers."""

    @pytest.mark.parametrize("module", [
        "src.routers.content_files",
        "src.routers.local_content",
    ])
    @pytest.mark.asyncio
    async def test_legacy_media_path_checks_access(self, db, org, admin_user, mock_request, module):
        import importlib
        mod = importlib.import_module(module)
        m = await _mk_media(db, org, public=True, name="gated")
        path = f"orgs/{org.org_uuid}/media/{m.media_uuid}/file.pdf"
        with patch("src.security.rbac.check_resource_access", new_callable=AsyncMock) as chk:
            await mod._check_content_access(path, admin_user, db, request=mock_request)
            assert chk.await_count == 1  # media access was enforced

    @pytest.mark.parametrize("module", [
        "src.routers.content_files",
        "src.routers.local_content",
    ])
    @pytest.mark.asyncio
    async def test_randomized_media_key_denied(self, db, org, admin_user, mock_request, module):
        import importlib
        mod = importlib.import_module(module)
        # A randomized storage dir (not 'media_...') is never resolvable by path → denied.
        path = f"orgs/{org.org_uuid}/media/randomtoken123/file.pdf"
        with pytest.raises(HTTPException) as exc:
            await mod._check_content_access(path, admin_user, db, request=mock_request)
        assert exc.value.status_code == 403
