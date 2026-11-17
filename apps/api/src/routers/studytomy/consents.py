"""Authenticated endpoints for a learner's own purpose-specific choices."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.studytomy.privacy import (
    StudyConsentEventCreate,
    StudyConsentEventRead,
    StudyConsentNoticeRead,
    StudyConsentPurpose,
)
from src.db.users import PublicUser
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.services.studytomy.consents import (
    get_my_consent_notice,
    list_my_consent_events,
    list_my_consent_events_for_self,
    record_my_consent_choice,
    record_my_consent_choice_for_self,
    withdraw_my_consent_choice,
    withdraw_my_consent_choice_for_self,
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


@router.get(
    "/orgs/{org_uuid}/learners/me/consent-notices/{purpose}",
    response_model=StudyConsentNoticeRead,
    summary="Read the current approved consent notice for the authenticated learner",
)
async def api_get_my_consent_notice(
    org_uuid: str,
    purpose: StudyConsentPurpose,
    current_user: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StudyConsentNoticeRead:
    organization = await _organization(org_uuid, db_session)
    return await get_my_consent_notice(
        db_session, organization, current_user, purpose
    )


@router.get(
    "/orgs/{org_uuid}/learners/me/consent-events",
    response_model=list[StudyConsentEventRead],
    summary="List the authenticated learner's own consent history",
)
async def api_list_my_consent_events_for_self(
    org_uuid: str,
    current_user: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[StudyConsentEventRead]:
    organization = await _organization(org_uuid, db_session)
    return await list_my_consent_events_for_self(
        db_session, organization, current_user, limit=limit
    )


@router.post(
    "/orgs/{org_uuid}/learners/me/consent-events",
    response_model=StudyConsentEventRead,
    status_code=201,
    summary="Append a consent choice for the authenticated learner",
)
async def api_record_my_consent_choice_for_self(
    org_uuid: str,
    request: StudyConsentEventCreate,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    current_user: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StudyConsentEventRead:
    organization = await _organization(org_uuid, db_session)
    return await record_my_consent_choice_for_self(
        db_session,
        organization,
        current_user,
        request,
        idempotency_key,
    )


@router.post(
    "/orgs/{org_uuid}/learners/me/consent-events/{event_uuid}/withdraw",
    response_model=StudyConsentEventRead,
    status_code=201,
    summary="Append a withdrawal for the authenticated learner",
)
async def api_withdraw_my_consent_choice_for_self(
    org_uuid: str,
    event_uuid: str,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    current_user: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StudyConsentEventRead:
    organization = await _organization(org_uuid, db_session)
    return await withdraw_my_consent_choice_for_self(
        db_session,
        organization,
        current_user,
        event_uuid,
        idempotency_key,
    )


@router.get(
    "/orgs/{org_uuid}/learners/{learner_uuid}/consent-events",
    response_model=list[StudyConsentEventRead],
    summary="List the authenticated learner's own consent history",
)
async def api_list_my_consent_events(
    org_uuid: str,
    learner_uuid: str,
    current_user: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[StudyConsentEventRead]:
    organization = await _organization(org_uuid, db_session)
    return await list_my_consent_events(
        db_session, organization, current_user, learner_uuid, limit=limit
    )


@router.post(
    "/orgs/{org_uuid}/learners/{learner_uuid}/consent-events",
    response_model=StudyConsentEventRead,
    status_code=201,
    summary="Append an explicit choice for one approved purpose",
)
async def api_record_my_consent_choice(
    org_uuid: str,
    learner_uuid: str,
    request: StudyConsentEventCreate,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    current_user: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StudyConsentEventRead:
    organization = await _organization(org_uuid, db_session)
    return await record_my_consent_choice(
        db_session,
        organization,
        current_user,
        learner_uuid,
        request,
        idempotency_key,
    )


@router.post(
    "/orgs/{org_uuid}/learners/{learner_uuid}/consent-events/{event_uuid}/withdraw",
    response_model=StudyConsentEventRead,
    status_code=201,
    summary="Append a withdrawal for a current consent choice",
)
async def api_withdraw_my_consent_choice(
    org_uuid: str,
    learner_uuid: str,
    event_uuid: str,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    current_user: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StudyConsentEventRead:
    organization = await _organization(org_uuid, db_session)
    return await withdraw_my_consent_choice(
        db_session,
        organization,
        current_user,
        learner_uuid,
        event_uuid,
        idempotency_key,
    )
