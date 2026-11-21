"""Server-persisted "Create Course" drafts (one per organization and user).

SECURITY: every operation is scoped to ``(org_id, current_user.id)`` taken from
the authenticated session, never from the request body, and requires the right
to create courses in that organization. Drafts are not courses: they never
count against plan limits and are never listed or readable by anyone else.
Draft thumbnails are stored in private content storage under
``orgs/{org_uuid}/studytomy/course-drafts/{draft_uuid}/{filename}``.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from fastapi.encoders import jsonable_encoder
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.course_creation_drafts import (
    COURSE_DRAFT_MAX_THUMBNAIL_BYTES,
    CourseCreationDraft,
    CourseDraftFields,
    CourseDraftRead,
)
from src.db.organizations import Organization
from src.db.users import PublicUser
from src.security.file_validation import get_safe_filename, validate_upload
from src.security.org_auth import require_org_create_permission
from src.services.utils.upload_content import delete_content, read_content, upload_content

logger = logging.getLogger(__name__)

DRAFT_TTL = timedelta(days=90)
CONFLICT_CODE = "COURSE_DRAFT_CONFLICT"


def _read(draft: CourseCreationDraft) -> CourseDraftRead:
    return CourseDraftRead(
        draft_uuid=draft.draft_uuid,
        name=draft.name,
        description=draft.description,
        learnings=draft.learnings,
        tags=draft.tags,
        public=draft.public,
        has_thumbnail=bool(draft.thumbnail_filename and draft.thumbnail_content_type),
        thumbnail_content_type=draft.thumbnail_content_type,
        revision=draft.revision,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _thumbnail_dir(draft_uuid: str) -> str:
    return f"studytomy/course-drafts/{draft_uuid}"


async def _org_uuid(db_session: AsyncSession, org_id: int) -> str:
    org_uuid = (
        await db_session.execute(select(Organization.org_uuid).where(Organization.id == org_id))
    ).scalar_one_or_none()
    if not org_uuid:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org_uuid


async def _best_effort_delete_thumbnail(org_uuid: str, draft_uuid: str, filename: Optional[str]) -> None:
    if not filename:
        return
    try:
        await delete_content(_thumbnail_dir(draft_uuid), "orgs", org_uuid, filename)
    except Exception:
        logger.warning("Failed to delete course draft thumbnail %s/%s", draft_uuid, filename, exc_info=True)


async def _authorize(current_user: PublicUser, org_id: int, db_session: AsyncSession) -> None:
    await require_org_create_permission(current_user, org_id, db_session, "courses")


async def _fetch(
    db_session: AsyncSession, org_id: int, user_id: int
) -> Optional[CourseCreationDraft]:
    statement = (
        select(CourseCreationDraft)
        .where(CourseCreationDraft.org_id == org_id, CourseCreationDraft.user_id == user_id)
        .execution_options(populate_existing=True)
    )
    return (await db_session.execute(statement)).scalars().first()


def _conflict(current: Optional[CourseCreationDraft]) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": CONFLICT_CODE,
            "message": "This draft was changed elsewhere. Reload it before saving again.",
            "draft": jsonable_encoder(_read(current)) if current else None,
        },
    )


async def _apply(
    db_session: AsyncSession,
    org_id: int,
    user_id: int,
    base_revision: Optional[int],
    values: dict,
    *,
    new_draft_uuid: Optional[str] = None,
) -> CourseDraftRead:
    """Create or update the owner's draft with compare-and-swap on ``revision``."""
    base = base_revision or 0
    now = datetime.now(timezone.utc)
    existing = await _fetch(db_session, org_id, user_id)

    if existing is None:
        if base != 0:
            raise _conflict(None)
        draft = CourseCreationDraft(
            draft_uuid=new_draft_uuid or f"coursedraft_{uuid4()}",
            org_id=org_id,
            user_id=user_id,
            created_at=now,
            updated_at=now,
            revision=1,
            **values,
        )
        db_session.add(draft)
        try:
            await db_session.commit()
        except IntegrityError:
            # A concurrent request created the draft first.
            await db_session.rollback()
            raise _conflict(await _fetch(db_session, org_id, user_id))
        return _read((await _fetch(db_session, org_id, user_id)) or draft)

    if existing.revision != base:
        raise _conflict(existing)

    result = await db_session.execute(
        update(CourseCreationDraft)
        .where(
            CourseCreationDraft.id == existing.id,  # type: ignore[arg-type]
            CourseCreationDraft.revision == base,  # type: ignore[arg-type]
        )
        .values(revision=base + 1, updated_at=now, **values)
    )
    if result.rowcount != 1:  # type: ignore[attr-defined]
        await db_session.rollback()
        raise _conflict(await _fetch(db_session, org_id, user_id))
    await db_session.commit()
    return _read(await _fetch(db_session, org_id, user_id))  # type: ignore[arg-type]


async def get_my_draft(
    db_session: AsyncSession, org_id: int, current_user: PublicUser
) -> Optional[CourseDraftRead]:
    await _authorize(current_user, org_id, db_session)
    draft = await _fetch(db_session, org_id, current_user.id)
    if draft is None:
        return None
    if datetime.now(timezone.utc) - _aware(draft.updated_at) > DRAFT_TTL:
        old_uuid = draft.draft_uuid
        old_filename = draft.thumbnail_filename
        await db_session.execute(
            delete(CourseCreationDraft).where(CourseCreationDraft.id == draft.id)  # type: ignore[arg-type]
        )
        await db_session.commit()
        if old_filename:
            org_uuid = await _org_uuid(db_session, org_id)
            await _best_effort_delete_thumbnail(org_uuid, old_uuid, old_filename)
        return None
    return _read(draft)


async def save_my_draft(
    db_session: AsyncSession, org_id: int, current_user: PublicUser, fields: CourseDraftFields
) -> CourseDraftRead:
    await _authorize(current_user, org_id, db_session)
    values = fields.model_dump(exclude={"base_revision"})
    return await _apply(db_session, org_id, current_user.id, fields.base_revision, values)


async def set_my_draft_thumbnail(
    db_session: AsyncSession,
    org_id: int,
    current_user: PublicUser,
    thumbnail: UploadFile,
    base_revision: Optional[int],
) -> CourseDraftRead:
    await _authorize(current_user, org_id, db_session)
    content_type, content = validate_upload(thumbnail, ["image"], COURSE_DRAFT_MAX_THUMBNAIL_BYTES)
    if len(content) > COURSE_DRAFT_MAX_THUMBNAIL_BYTES:
        raise HTTPException(status_code=413, detail="Thumbnail is too large")

    org_uuid = await _org_uuid(db_session, org_id)
    existing = await _fetch(db_session, org_id, current_user.id)
    base = base_revision or 0
    if existing is None and base != 0:
        raise _conflict(None)
    if existing is not None and existing.revision != base:
        raise _conflict(existing)

    target_draft_uuid = existing.draft_uuid if existing is not None else f"coursedraft_{uuid4()}"
    previous_filename = existing.thumbnail_filename if existing is not None else None
    filename = get_safe_filename(
        thumbnail.filename or "thumbnail",
        f"{uuid4()}_draft_thumb",
        content_type=content_type,
    )

    await upload_content(
        directory=_thumbnail_dir(target_draft_uuid),
        type_of_dir="orgs",
        uuid=org_uuid,
        file_binary=content,
        file_and_format=filename,
        allowed_formats=None,
    )
    try:
        updated = await _apply(
            db_session,
            org_id,
            current_user.id,
            base_revision,
            {"thumbnail_filename": filename, "thumbnail_content_type": content_type},
            new_draft_uuid=target_draft_uuid,
        )
    except Exception:
        await _best_effort_delete_thumbnail(org_uuid, target_draft_uuid, filename)
        raise

    if previous_filename and previous_filename != filename:
        await _best_effort_delete_thumbnail(org_uuid, target_draft_uuid, previous_filename)
    return updated


async def clear_my_draft_thumbnail(
    db_session: AsyncSession, org_id: int, current_user: PublicUser, base_revision: Optional[int]
) -> CourseDraftRead:
    await _authorize(current_user, org_id, db_session)
    existing = await _fetch(db_session, org_id, current_user.id)
    previous_filename = existing.thumbnail_filename if existing is not None else None
    previous_uuid = existing.draft_uuid if existing is not None else None
    updated = await _apply(
        db_session,
        org_id,
        current_user.id,
        base_revision,
        {"thumbnail_filename": None, "thumbnail_content_type": None},
    )
    if previous_uuid and previous_filename:
        org_uuid = await _org_uuid(db_session, org_id)
        await _best_effort_delete_thumbnail(org_uuid, previous_uuid, previous_filename)
    return updated


async def get_my_draft_thumbnail(
    db_session: AsyncSession, org_id: int, current_user: PublicUser
) -> tuple[bytes, str]:
    await _authorize(current_user, org_id, db_session)
    draft = await _fetch(db_session, org_id, current_user.id)
    if draft is None or not draft.thumbnail_filename or not draft.thumbnail_content_type:
        raise HTTPException(status_code=404, detail="Draft thumbnail not found")
    org_uuid = await _org_uuid(db_session, org_id)
    data = await read_content(
        directory=_thumbnail_dir(draft.draft_uuid),
        type_of_dir="orgs",
        uuid=org_uuid,
        file_and_format=draft.thumbnail_filename,
    )
    return data, draft.thumbnail_content_type


async def delete_my_draft(
    db_session: AsyncSession, org_id: int, current_user: PublicUser, base_revision: Optional[int]
) -> None:
    """Discard the draft. With ``base_revision`` the delete refuses stale callers."""
    await _authorize(current_user, org_id, db_session)
    existing = await _fetch(db_session, org_id, current_user.id)
    if existing is None:
        return
    if base_revision is not None and existing.revision != base_revision:
        raise _conflict(existing)
    old_uuid = existing.draft_uuid
    old_filename = existing.thumbnail_filename
    await db_session.execute(
        delete(CourseCreationDraft).where(CourseCreationDraft.id == existing.id)  # type: ignore[arg-type]
    )
    await db_session.commit()
    if old_filename:
        org_uuid = await _org_uuid(db_session, org_id)
        await _best_effort_delete_thumbnail(org_uuid, old_uuid, old_filename)
