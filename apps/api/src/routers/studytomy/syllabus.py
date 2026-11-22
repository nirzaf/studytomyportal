"""Session-only educator source library and student topic exploration routes."""

import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.users import PublicUser
from src.routers.studytomy.private_route import PRIVATE_HEADERS, PrivateStudyTomyRoute
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.security.org_auth import require_org_membership
from src.services.studytomy.syllabus_library import (
    ExploreTopicRequest,
    ExploreTopicResponse,
    SyllabusMappingCreate,
    SyllabusMappingRead,
    SyllabusMappingRevocation,
    SyllabusMappingReview,
    SyllabusSourceList,
    SyllabusSourcePages,
    SyllabusSourceReview,
    SyllabusSourceUpload,
    SyllabusSourceVersionRead,
    ActivitySyllabusSourceRead,
    SyllabusCurriculumOption,
    SyllabusConceptOption,
    create_syllabus_mapping,
    explore_topic,
    list_published_curricula,
    list_published_curriculum_concepts,
    list_activity_syllabus_sources,
    list_source_versions,
    list_syllabus_mappings,
    read_source_pages,
    read_syllabus_pdf,
    retry_syllabus_index,
    review_syllabus_mapping,
    review_syllabus_source,
    revoke_syllabus_mapping,
    revoke_syllabus_source,
    upload_syllabus_source,
)

router = APIRouter(route_class=PrivateStudyTomyRoute)


class SyllabusRevocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: Annotated[str, StringConstraints(strip_whitespace=True, min_length=8, max_length=500)]


async def _organization(org_uuid: str, actor: PublicUser, db: AsyncSession) -> Organization:
    org = (await db.execute(select(Organization).where(Organization.org_uuid == org_uuid))).scalar_one_or_none()
    if org is None:
        raise HTTPException(404, "Organization not found.")
    await require_org_membership(actor.id, org.id, db)
    return org


def _form_metadata(raw: str) -> SyllabusSourceUpload:
    try:
        value = json.loads(raw)
        return SyllabusSourceUpload.model_validate(value)
    except (ValueError, ValidationError) as exc:
        detail = "Invalid source metadata."
        if isinstance(exc, ValidationError) and exc.errors():
            detail = f"Invalid source metadata: {exc.errors()[0].get('msg', 'validation failed')}"
        raise HTTPException(422, detail) from None


@router.get("/orgs/{org_uuid}/syllabus-sources", response_model=SyllabusSourceList)
async def api_list_syllabus_sources(
    org_uuid: str,
    response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SyllabusSourceList:
    org = await _organization(org_uuid, actor, db)
    result = await list_source_versions(db, org, actor, limit=limit, offset=offset)
    response.headers.update(PRIVATE_HEADERS)
    return result


@router.post("/orgs/{org_uuid}/syllabus-sources", response_model=SyllabusSourceVersionRead, status_code=201)
async def api_upload_syllabus_source(
    org_uuid: str,
    response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    file: Annotated[UploadFile, File()],
    metadata: Annotated[str, Form(description="JSON metadata for this immutable PDF version")],
) -> SyllabusSourceVersionRead:
    org = await _organization(org_uuid, actor, db)
    receipt = await upload_syllabus_source(db, org, actor, file, _form_metadata(metadata))
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        if not receipt.duplicate:
            from src.services.courses.transfer.storage_utils import delete_storage_file
            import asyncio

            await asyncio.to_thread(delete_storage_file, f"content/orgs/{org.org_uuid}/studytomy/syllabus-library/{receipt.source_uuid}/{receipt.version_uuid}.pdf")
        raise
    response.headers.update(PRIVATE_HEADERS)
    return receipt


@router.get(
    "/orgs/{org_uuid}/syllabus-sources/{version_uuid}/pages",
    response_model=SyllabusSourcePages,
)
async def api_read_syllabus_pages(
    org_uuid: str,
    version_uuid: str,
    response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SyllabusSourcePages:
    org = await _organization(org_uuid, actor, db)
    result = await read_source_pages(db, org, actor, version_uuid)
    response.headers.update(PRIVATE_HEADERS)
    return result


@router.post(
    "/orgs/{org_uuid}/syllabus-sources/{version_uuid}/review",
    response_model=SyllabusSourceVersionRead,
)
async def api_review_syllabus_source(
    org_uuid: str,
    version_uuid: str,
    command: SyllabusSourceReview,
    response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SyllabusSourceVersionRead:
    org = await _organization(org_uuid, actor, db)
    result = await review_syllabus_source(db, org, actor, version_uuid, command)
    await db.commit()
    response.headers.update(PRIVATE_HEADERS)
    return result


@router.post(
    "/orgs/{org_uuid}/syllabus-sources/{version_uuid}/retry-index",
    response_model=SyllabusSourceVersionRead,
)
async def api_retry_syllabus_index(
    org_uuid: str,
    version_uuid: str,
    response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SyllabusSourceVersionRead:
    org = await _organization(org_uuid, actor, db)
    result = await retry_syllabus_index(db, org, actor, version_uuid)
    await db.commit()
    response.headers.update(PRIVATE_HEADERS)
    return result


@router.post(
    "/orgs/{org_uuid}/syllabus-sources/{version_uuid}/revoke",
    response_model=SyllabusSourceVersionRead,
)
async def api_revoke_syllabus_source(
    org_uuid: str,
    version_uuid: str,
    command: SyllabusRevocation,
    response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SyllabusSourceVersionRead:
    org = await _organization(org_uuid, actor, db)
    result = await revoke_syllabus_source(db, org, actor, version_uuid, command.evidence)
    await db.commit()
    response.headers.update(PRIVATE_HEADERS)
    return result


@router.get("/orgs/{org_uuid}/syllabus-mappings", response_model=list[SyllabusMappingRead])
async def api_list_syllabus_mappings(
    org_uuid: str,
    response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SyllabusMappingRead]:
    org = await _organization(org_uuid, actor, db)
    result, next_offset = await list_syllabus_mappings(db, org, actor, limit=limit, offset=offset)
    if next_offset is not None:
        response.headers["X-Next-Offset"] = str(next_offset)
    response.headers.update(PRIVATE_HEADERS)
    return result


@router.post("/orgs/{org_uuid}/syllabus-mappings", response_model=SyllabusMappingRead, status_code=201)
async def api_create_syllabus_mapping(
    org_uuid: str,
    command: SyllabusMappingCreate,
    response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SyllabusMappingRead:
    org = await _organization(org_uuid, actor, db)
    result = await create_syllabus_mapping(db, org, actor, command)
    await db.commit()
    response.headers.update(PRIVATE_HEADERS)
    return result


@router.post(
    "/orgs/{org_uuid}/syllabus-mappings/{mapping_uuid}/review",
    response_model=SyllabusMappingRead,
)
async def api_review_syllabus_mapping(
    org_uuid: str,
    mapping_uuid: str,
    command: SyllabusMappingReview,
    response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SyllabusMappingRead:
    org = await _organization(org_uuid, actor, db)
    result = await review_syllabus_mapping(db, org, actor, mapping_uuid, command)
    await db.commit()
    response.headers.update(PRIVATE_HEADERS)
    return result


@router.post(
    "/orgs/{org_uuid}/syllabus-mappings/{mapping_uuid}/revoke",
    response_model=SyllabusMappingRead,
)
async def api_revoke_syllabus_mapping(
    org_uuid: str,
    mapping_uuid: str,
    command: SyllabusMappingRevocation,
    response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SyllabusMappingRead:
    org = await _organization(org_uuid, actor, db)
    result = await revoke_syllabus_mapping(db, org, actor, mapping_uuid, command.evidence)
    await db.commit()
    response.headers.update(PRIVATE_HEADERS)
    return result


@router.get("/orgs/{org_uuid}/explore/curricula", response_model=list[SyllabusCurriculumOption])
async def api_explore_curricula(
    org_uuid: str,
    response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[SyllabusCurriculumOption]:
    org = await _organization(org_uuid, actor, db)
    result = await list_published_curricula(db, org)
    response.headers.update(PRIVATE_HEADERS)
    return result


@router.get(
    "/orgs/{org_uuid}/syllabus-curricula/{curriculum_uuid}/concepts",
    response_model=list[SyllabusConceptOption],
)
async def api_list_syllabus_curriculum_concepts(
    org_uuid: str,
    curriculum_uuid: str,
    response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[SyllabusConceptOption]:
    org = await _organization(org_uuid, actor, db)
    result = await list_published_curriculum_concepts(db, org, actor, curriculum_uuid)
    response.headers.update(PRIVATE_HEADERS)
    return result


@router.post("/orgs/{org_uuid}/explore/topics", response_model=ExploreTopicResponse)
async def api_explore_topic(
    org_uuid: str,
    command: ExploreTopicRequest,
    response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> ExploreTopicResponse:
    org = await _organization(org_uuid, actor, db)
    result = await explore_topic(db, org, actor, command)
    response.headers.update(PRIVATE_HEADERS)
    return result


@router.get(
    "/orgs/{org_uuid}/course-activities/{activity_uuid}/syllabus-sources",
    response_model=list[ActivitySyllabusSourceRead],
)
async def api_list_activity_syllabus_sources(
    org_uuid: str,
    activity_uuid: str,
    request: Request,
    response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[ActivitySyllabusSourceRead]:
    org = await _organization(org_uuid, actor, db)
    result = await list_activity_syllabus_sources(request, db, org, actor, activity_uuid)
    response.headers.update(PRIVATE_HEADERS)
    return result


@router.get("/orgs/{org_uuid}/syllabus-sources/{version_uuid}/pdf")
async def api_read_syllabus_pdf(
    org_uuid: str,
    version_uuid: str,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    org = await _organization(org_uuid, actor, db)
    content, filename = await read_syllabus_pdf(db, org, actor, version_uuid)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            **PRIVATE_HEADERS,
            "Content-Disposition": f'inline; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        },
    )
