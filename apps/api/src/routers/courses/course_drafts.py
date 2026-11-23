from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.courses.course_creation_drafts import CourseDraftFields, CourseDraftRead
from src.db.users import PublicUser
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.services.courses.course_drafts import (
    clear_my_draft_thumbnail,
    delete_my_draft,
    get_my_draft,
    get_my_draft_thumbnail,
    save_my_draft,
    set_my_draft_thumbnail,
)

# Mounted under /course-drafts. Session users only: API tokens cannot hold a
# personal in-progress form.
router = APIRouter()


@router.get(
    "/orgs/{org_id}/me",
    response_model=Optional[CourseDraftRead],
    summary="Get my unfinished Create Course draft",
    description="Returns the caller's draft for the organization, or null when there is none.",
)
async def api_get_my_course_draft(
    org_id: int,
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    return await get_my_draft(db_session, org_id, current_user)


@router.put(
    "/orgs/{org_id}/me",
    response_model=CourseDraftRead,
    summary="Create or update my Create Course draft",
    description=(
        "Incomplete forms are accepted. base_revision must be the revision last "
        "seen (omit when no draft exists); a stale value returns 409 with the current draft."
    ),
    responses={409: {"description": "Draft was changed elsewhere"}},
)
async def api_save_my_course_draft(
    org_id: int,
    fields: CourseDraftFields,
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    return await save_my_draft(db_session, org_id, current_user, fields)


@router.delete(
    "/orgs/{org_id}/me",
    status_code=204,
    summary="Discard my Create Course draft",
    responses={409: {"description": "Draft was changed elsewhere"}},
)
async def api_delete_my_course_draft(
    org_id: int,
    base_revision: Optional[int] = Query(default=None, ge=0),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    await delete_my_draft(db_session, org_id, current_user, base_revision)
    return Response(status_code=204)


@router.put(
    "/orgs/{org_id}/me/thumbnail",
    response_model=CourseDraftRead,
    summary="Attach a thumbnail image to my draft",
    responses={409: {"description": "Draft was changed elsewhere"}},
)
async def api_set_my_course_draft_thumbnail(
    org_id: int,
    thumbnail: UploadFile = File(),
    base_revision: Optional[int] = Form(default=None, ge=0),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    return await set_my_draft_thumbnail(db_session, org_id, current_user, thumbnail, base_revision)


@router.delete(
    "/orgs/{org_id}/me/thumbnail",
    response_model=CourseDraftRead,
    summary="Remove the thumbnail from my draft",
    responses={409: {"description": "Draft was changed elsewhere"}},
)
async def api_clear_my_course_draft_thumbnail(
    org_id: int,
    base_revision: Optional[int] = Query(default=None, ge=0),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    return await clear_my_draft_thumbnail(db_session, org_id, current_user, base_revision)


@router.get(
    "/orgs/{org_id}/me/thumbnail",
    summary="Download my draft thumbnail",
    response_class=Response,
)
async def api_get_my_course_draft_thumbnail(
    org_id: int,
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    data, content_type = await get_my_draft_thumbnail(db_session, org_id, current_user)
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )
