"""Authenticated private evidence upload and download endpoints."""

from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.studytomy.operations import StudyEvidenceFileRead
from src.db.users import PublicUser
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.services.studytomy.content_evidence import (
    ContentEvidencePurpose,
    ContentEvidenceRead,
    ContentReviewCandidate,
    ContentReviewEvidenceSelection,
    CourseReviewReceipt,
    get_course_content_candidate,
    open_course_evidence,
    upload_course_evidence,
    verify_course_review_evidence,
)
from src.services.studytomy.evidence_files import (
    list_eligibility_evidence,
    list_relationship_evidence,
    open_eligibility_evidence,
    open_relationship_evidence,
    upload_eligibility_evidence,
    upload_relationship_evidence,
)
from src.services.studytomy.publication import (
    CourseReleasePreflight,
    CourseReleaseReceipt,
    CourseReviewCommand,
    designate_managed_course,
    record_course_release,
    record_course_review,
    verify_course_release,
)

router = APIRouter()


async def _organization(org_uuid: str, db_session: AsyncSession) -> Organization:
    result = await db_session.execute(
        select(Organization).where(Organization.org_uuid == org_uuid)
    )
    organization = result.scalars().first()
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return organization


@router.put(
    "/orgs/{org_uuid}/courses/{course_uuid}/managed-draft",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def api_designate_managed_course(
    org_uuid: str, course_uuid: str, response: Response,
    current_user: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    organization = await _organization(org_uuid, db_session)
    await designate_managed_course(db_session, organization, current_user, course_uuid)
    response.headers["Cache-Control"] = "private, no-store"


@router.get("/orgs/{org_uuid}/courses/{course_uuid}/content-review-candidate")
async def api_content_review_candidate(
    org_uuid: str, course_uuid: str, response: Response,
    current_user: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
    before_id: Annotated[int | None, Query(ge=1, le=9223372036854775807)] = None,
) -> ContentReviewCandidate:
    organization = await _organization(org_uuid, db_session)
    response.headers["Cache-Control"] = "private, no-store"
    return await get_course_content_candidate(
        db_session, organization, current_user, course_uuid, before_id=before_id
    )


@router.post(
    "/orgs/{org_uuid}/courses/{course_uuid}/content-review-decisions/{review_uuid}/release",
    status_code=status.HTTP_201_CREATED,
)
async def api_record_course_release(
    org_uuid: str, course_uuid: str, review_uuid: str, response: Response,
    current_user: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CourseReleaseReceipt:
    organization = await _organization(org_uuid, db_session)
    response.headers["Cache-Control"] = "private, no-store"
    return await record_course_release(db_session, organization, current_user, course_uuid, review_uuid)


@router.post("/orgs/{org_uuid}/courses/{course_uuid}/content-review-decisions/{review_uuid}/release-preflight")
async def api_verify_course_release(
    org_uuid: str, course_uuid: str, review_uuid: str, response: Response,
    current_user: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CourseReleasePreflight:
    organization = await _organization(org_uuid, db_session)
    response.headers["Cache-Control"] = "private, no-store"
    return await verify_course_release(db_session, organization, current_user, course_uuid, review_uuid)


@router.post(
    "/orgs/{org_uuid}/courses/{course_uuid}/content-review-decisions",
    status_code=status.HTTP_201_CREATED,
)
async def api_record_course_review(
    org_uuid: str, course_uuid: str, command: CourseReviewCommand, response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    current_user: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
) -> CourseReviewReceipt:
    organization = await _organization(org_uuid, db_session)
    response.headers["Cache-Control"] = "private, no-store"
    return await record_course_review(db_session, organization, current_user, course_uuid, command, idempotency_key)


@router.post(
    "/orgs/{org_uuid}/courses/{course_uuid}/content-review-candidate/verify-evidence",
    response_model=list[ContentEvidenceRead],
)
async def api_verify_course_review_evidence(
    org_uuid: str, course_uuid: str, selection: ContentReviewEvidenceSelection, response: Response,
    current_user: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[ContentEvidenceRead]:
    organization = await _organization(org_uuid, db_session)
    response.headers["Cache-Control"] = "private, no-store"
    return await verify_course_review_evidence(
        db_session, organization, current_user, course_uuid, selection
    )


@router.post(
    "/orgs/{org_uuid}/courses/{course_uuid}/evidence-files",
    response_model=ContentEvidenceRead, status_code=status.HTTP_201_CREATED,
)
async def api_upload_course_evidence(
    org_uuid: str, course_uuid: str, response: Response,
    purpose: Annotated[ContentEvidencePurpose, Form()],
    revision_sha256: Annotated[str, Form(pattern=r"^[0-9a-f]{64}$")],
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    current_user: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ContentEvidenceRead:
    organization = await _organization(org_uuid, db_session)
    response.headers["Cache-Control"] = "private, no-store"
    return await upload_course_evidence(
        db_session, organization, current_user, course_uuid, revision_sha256, purpose, file, idempotency_key,
    )


@router.get("/orgs/{org_uuid}/courses/{course_uuid}/evidence-files/{evidence_uuid}/content")
async def api_download_course_evidence(
    org_uuid: str, course_uuid: str, evidence_uuid: str,
    current_user: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StreamingResponse:
    organization = await _organization(org_uuid, db_session)
    evidence, content = await open_course_evidence(db_session, organization, current_user, course_uuid, evidence_uuid)
    return _evidence_download(evidence.evidence_uuid, evidence.media_type, content)


@router.post(
    "/orgs/{org_uuid}/learners/{learner_uuid}/evidence-files",
    response_model=StudyEvidenceFileRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload private eligibility evidence for a scoped learner",
)
async def api_upload_eligibility_evidence(
    org_uuid: str,
    learner_uuid: str,
    file: UploadFile = File(...),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyEvidenceFileRead:
    organization = await _organization(org_uuid, db_session)
    return await upload_eligibility_evidence(
        db_session,
        organization,
        current_user,
        learner_uuid,
        file,
        idempotency_key,
    )


@router.get(
    "/orgs/{org_uuid}/learners/{learner_uuid}/evidence-files",
    response_model=list[StudyEvidenceFileRead],
    summary="List private eligibility evidence for a scoped learner",
)
async def api_list_eligibility_evidence(
    org_uuid: str,
    learner_uuid: str,
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> list[StudyEvidenceFileRead]:
    organization = await _organization(org_uuid, db_session)
    return await list_eligibility_evidence(
        db_session,
        organization,
        current_user,
        learner_uuid,
    )


@router.get(
    "/orgs/{org_uuid}/learners/{learner_uuid}/evidence-files/{evidence_uuid}/content",
    summary="Stream private learner evidence after a fresh permission check",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def api_download_eligibility_evidence(
    org_uuid: str,
    learner_uuid: str,
    evidence_uuid: str,
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    organization = await _organization(org_uuid, db_session)
    evidence, content = await open_eligibility_evidence(
        db_session,
        organization,
        current_user,
        learner_uuid,
        evidence_uuid,
    )
    return _evidence_download(evidence.evidence_uuid, evidence.media_type, content)


@router.post(
    "/orgs/{org_uuid}/relationships/{relationship_uuid}/evidence-files",
    response_model=StudyEvidenceFileRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload private guardian-authority evidence bound to one relationship request",
)
async def api_upload_relationship_evidence(
    org_uuid: str,
    relationship_uuid: str,
    file: UploadFile = File(...),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyEvidenceFileRead:
    organization = await _organization(org_uuid, db_session)
    return await upload_relationship_evidence(
        db_session,
        organization,
        current_user,
        relationship_uuid,
        file,
        idempotency_key,
    )


@router.get(
    "/orgs/{org_uuid}/relationships/{relationship_uuid}/evidence-files",
    response_model=list[StudyEvidenceFileRead],
    summary="List private guardian-authority evidence bound to one relationship request",
)
async def api_list_relationship_evidence(
    org_uuid: str,
    relationship_uuid: str,
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> list[StudyEvidenceFileRead]:
    organization = await _organization(org_uuid, db_session)
    return await list_relationship_evidence(
        db_session,
        organization,
        current_user,
        relationship_uuid,
    )


@router.get(
    "/orgs/{org_uuid}/relationships/{relationship_uuid}/evidence-files/{evidence_uuid}/content",
    summary="Stream private guardian-authority evidence after a fresh permission check",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def api_download_relationship_evidence(
    org_uuid: str,
    relationship_uuid: str,
    evidence_uuid: str,
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    organization = await _organization(org_uuid, db_session)
    evidence, content = await open_relationship_evidence(
        db_session,
        organization,
        current_user,
        relationship_uuid,
        evidence_uuid,
    )
    return _evidence_download(evidence.evidence_uuid, evidence.media_type, content)


def _evidence_download(evidence_uuid, media_type, content) -> StreamingResponse:
    extension = {
        "application/pdf": ".pdf",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }.get(media_type, ".bin")
    return StreamingResponse(
        content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="evidence-{evidence_uuid}{extension}"',
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
    )
