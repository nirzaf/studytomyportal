"""Authenticated intake for managed-course draft source files."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.users import PublicUser
from src.routers.studytomy.private_route import PRIVATE_HEADERS, PrivateStudyTomyRoute
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.services.studytomy.grounding import (
    DraftMaterialPage, DraftMaterialReceipt, GroundedLessonPreview, GroundedLessonRequest, GroundedQuizPreview,
    GroundedLessonSave, GroundedLessonSources, GroundedQuizSave, GroundedQuizSources,
    SaveGroundedLessonRequest, SaveGroundedQuizRequest,
    preview_grounded_lesson,
    preview_grounded_quiz, read_grounded_lesson_sources, read_grounded_quiz_sources, revoke_draft_material, save_grounded_lesson,
    list_draft_materials, save_grounded_quiz, upload_draft_material,
)

router = APIRouter(route_class=PrivateStudyTomyRoute)
_LESSON = "/orgs/{org_uuid}/courses/{course_uuid}/lessons/{lesson_uuid}"


@router.get(_LESSON + "/draft-sources")
async def list_sources(
    org_uuid: str, course_uuid: str, lesson_uuid: str, request: Request, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    after: UUID | None = None,
) -> DraftMaterialPage:
    materials = await list_draft_materials(db, request, actor, org_uuid, course_uuid, lesson_uuid, after)
    response.headers.update(PRIVATE_HEADERS)
    return materials


@router.post(_LESSON + "/draft-sources", status_code=201)
async def upload_source(
    org_uuid: str, course_uuid: str, lesson_uuid: str, request: Request, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    file: Annotated[UploadFile, File()],
) -> DraftMaterialReceipt:
    receipt = await upload_draft_material(db, request, actor, org_uuid, course_uuid, lesson_uuid, file)
    await db.commit()
    response.headers.update(PRIVATE_HEADERS)
    return receipt


@router.post(_LESSON + "/grounded-lesson-preview")
async def preview_lesson(
    org_uuid: str, course_uuid: str, lesson_uuid: str,
    command: GroundedLessonRequest, request: Request, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> GroundedLessonPreview:
    draft = await preview_grounded_lesson(db, request, actor, org_uuid, course_uuid, lesson_uuid, command)
    response.headers.update(PRIVATE_HEADERS)
    return draft


@router.post(_LESSON + "/grounded-quiz-preview")
async def preview_quiz(
    org_uuid: str, course_uuid: str, lesson_uuid: str,
    command: GroundedLessonRequest, request: Request, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> GroundedQuizPreview:
    draft = await preview_grounded_quiz(db, request, actor, org_uuid, course_uuid, lesson_uuid, command)
    response.headers.update(PRIVATE_HEADERS)
    return draft


@router.post(_LESSON + "/grounded-lesson-draft")
async def save_lesson(
    org_uuid: str, course_uuid: str, lesson_uuid: str,
    command: SaveGroundedLessonRequest, request: Request, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> GroundedLessonSave:
    saved = await save_grounded_lesson(db, request, actor, org_uuid, course_uuid, lesson_uuid, command)
    response.headers.update(PRIVATE_HEADERS)
    return saved


@router.get(_LESSON + "/grounded-lesson-sources")
async def lesson_sources(
    org_uuid: str, course_uuid: str, lesson_uuid: str, request: Request, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> GroundedLessonSources:
    sources = await read_grounded_lesson_sources(db, request, actor, org_uuid, course_uuid, lesson_uuid)
    response.headers.update(PRIVATE_HEADERS)
    return sources


@router.post(_LESSON + "/grounded-quiz-draft", status_code=201)
async def save_quiz(
    org_uuid: str, course_uuid: str, lesson_uuid: str,
    command: SaveGroundedQuizRequest, request: Request, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> GroundedQuizSave:
    saved = await save_grounded_quiz(db, request, actor, org_uuid, course_uuid, lesson_uuid, command)
    response.headers.update(PRIVATE_HEADERS)
    return saved


@router.post(_LESSON + "/draft-sources/{source_uuid}/revoke", status_code=204)
async def revoke_source(
    org_uuid: str, course_uuid: str, lesson_uuid: str, source_uuid: UUID, request: Request,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    await revoke_draft_material(db, request, actor, org_uuid, course_uuid, lesson_uuid, source_uuid)
    await db.commit()
    return Response(status_code=204, headers=PRIVATE_HEADERS)


@router.get(_LESSON + "/assignments/{assignment_uuid}/tasks/{task_uuid}/grounded-quiz-sources")
async def quiz_sources(
    org_uuid: str, course_uuid: str, lesson_uuid: str, assignment_uuid: str, task_uuid: str,
    request: Request, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> GroundedQuizSources:
    sources = await read_grounded_quiz_sources(
        db, request, actor, org_uuid, course_uuid, lesson_uuid, assignment_uuid, task_uuid,
    )
    response.headers.update(PRIVATE_HEADERS)
    return sources
