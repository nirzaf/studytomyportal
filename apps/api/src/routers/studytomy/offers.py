"""Authenticated, organization-scoped endpoints for private offer drafts."""

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.studytomy.commerce import (
    StudyOfferDraftCreate,
    StudyOfferDraftRead,
    StudyOfferDraftRevision,
)
from src.db.users import PublicUser
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.services.studytomy.offers import (
    OfferDraftReadiness,
    create_offer_draft,
    get_offer_draft,
    get_offer_draft_readiness,
    revise_offer_draft,
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


@router.post(
    "/orgs/{org_uuid}/offers/drafts",
    response_model=StudyOfferDraftRead,
    status_code=201,
    summary="Create a private, non-purchasable offer draft",
)
async def api_create_offer_draft(
    org_uuid: str,
    request: StudyOfferDraftCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyOfferDraftRead:
    organization = await _organization(org_uuid, db_session)
    return await create_offer_draft(
        db_session,
        organization,
        current_user,
        request,
        idempotency_key,
    )


@router.get(
    "/orgs/{org_uuid}/offers/{offer_uuid}/draft",
    response_model=StudyOfferDraftRead,
    summary="Read an assigned private offer draft version",
)
async def api_get_offer_draft(
    org_uuid: str,
    offer_uuid: str,
    version_no: int | None = Query(default=None, ge=1),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyOfferDraftRead:
    organization = await _organization(org_uuid, db_session)
    return await get_offer_draft(
        db_session,
        organization,
        current_user,
        offer_uuid,
        version_no,
    )


@router.get("/orgs/{org_uuid}/offers/{offer_uuid}/draft/readiness")
async def api_get_offer_draft_readiness(
    org_uuid: str,
    offer_uuid: str,
    response: Response,
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> OfferDraftReadiness:
    organization = await _organization(org_uuid, db_session)
    readiness = await get_offer_draft_readiness(db_session, organization, current_user, offer_uuid)
    response.headers["Cache-Control"] = "private, no-store"
    return readiness


@router.put(
    "/orgs/{org_uuid}/offers/{offer_uuid}/draft",
    response_model=StudyOfferDraftRead,
    summary="Append a new immutable offer draft version",
)
async def api_revise_offer_draft(
    org_uuid: str,
    offer_uuid: str,
    request: StudyOfferDraftRevision,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyOfferDraftRead:
    organization = await _organization(org_uuid, db_session)
    return await revise_offer_draft(
        db_session,
        organization,
        current_user,
        offer_uuid,
        request,
        idempotency_key,
    )
