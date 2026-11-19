"""Authenticated endpoints for individual learner identity and eligibility."""

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.studytomy.family import (
    StudyEligibilityDecisionCreate,
    StudyEligibilityDecisionRead,
    StudyFamilyReviewQueueItem,
    StudyLearnerActivationCreate,
    StudyLearnerProfileCreate,
    StudyLearnerProfileRead,
    StudyRelationshipDecisionCreate,
    StudyRelationshipRead,
    StudyRelationshipRequestCreate,
    StudyRelationshipRestrictionCreate,
    StudyRelationshipReviewRead,
    StudyRelationshipWithdrawalCreate,
    StudySharingGrantCreate,
    StudySharingGrantRead,
    StudySharingGrantWithdrawal,
)
from src.db.users import PublicUser
from src.routers.studytomy.private_route import PRIVATE_HEADERS, PrivateStudyTomyRoute
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.services.studytomy.authority import (
    create_relationship_request,
    decide_relationship_request,
    get_relationship_for_review,
    list_family_review_queue,
    list_my_relationship_requests,
    restrict_relationship_authority,
    withdraw_my_relationship_request,
)
from src.services.studytomy.learners import (
    activate_learner_profile,
    create_learner_profile,
    get_learner_profile,
    record_eligibility_decision,
)
from src.services.studytomy.sharing import (
    create_my_sharing_grant,
    list_my_sharing_grants,
    withdraw_my_sharing_grant,
)

def _mark_private(response: Response) -> None:
    response.headers.update(PRIVATE_HEADERS)


router = APIRouter(route_class=PrivateStudyTomyRoute, dependencies=[Depends(_mark_private)])


async def _organization(org_uuid: str, db_session: AsyncSession) -> Organization:
    result = await db_session.execute(
        select(Organization).where(Organization.org_uuid == org_uuid)
    )
    organization = result.scalars().first()
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return organization


@router.post(
    "/orgs/{org_uuid}/learners",
    response_model=StudyLearnerProfileRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a pending individual learner profile",
)
async def api_create_learner_profile(
    org_uuid: str,
    request: StudyLearnerProfileCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyLearnerProfileRead:
    organization = await _organization(org_uuid, db_session)
    return await create_learner_profile(
        db_session,
        organization,
        current_user,
        request,
        idempotency_key,
    )


@router.get(
    "/orgs/{org_uuid}/learners/{learner_uuid}",
    response_model=StudyLearnerProfileRead,
    summary="Read a scoped learner profile and current eligibility decisions",
)
async def api_get_learner_profile(
    org_uuid: str,
    learner_uuid: str,
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyLearnerProfileRead:
    organization = await _organization(org_uuid, db_session)
    return await get_learner_profile(
        db_session,
        organization,
        current_user,
        learner_uuid,
    )


@router.post(
    "/orgs/{org_uuid}/learners/{learner_uuid}/eligibility-decisions",
    response_model=StudyEligibilityDecisionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Record a policy-bound human learner eligibility decision",
)
async def api_record_eligibility_decision(
    org_uuid: str,
    learner_uuid: str,
    request: StudyEligibilityDecisionCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyEligibilityDecisionRead:
    organization = await _organization(org_uuid, db_session)
    return await record_eligibility_decision(
        db_session,
        organization,
        current_user,
        learner_uuid,
        request,
        idempotency_key,
    )


@router.post(
    "/orgs/{org_uuid}/learners/{learner_uuid}/activate",
    response_model=StudyLearnerProfileRead,
    summary="Activate a reviewed pending learner profile",
)
async def api_activate_learner_profile(
    org_uuid: str,
    learner_uuid: str,
    request: StudyLearnerActivationCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyLearnerProfileRead:
    organization = await _organization(org_uuid, db_session)
    return await activate_learner_profile(
        db_session,
        organization,
        current_user,
        learner_uuid,
        request,
        idempotency_key,
    )


@router.post(
    "/orgs/{org_uuid}/learners/{learner_uuid}/relationships",
    response_model=StudyRelationshipRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit a pending guardian-authority request",
)
async def api_create_relationship_request(
    org_uuid: str,
    learner_uuid: str,
    request: StudyRelationshipRequestCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyRelationshipRead:
    organization = await _organization(org_uuid, db_session)
    return await create_relationship_request(
        db_session,
        organization,
        current_user,
        learner_uuid,
        request,
        idempotency_key,
    )


@router.get(
    "/orgs/{org_uuid}/my-relationships",
    response_model=list[StudyRelationshipRead],
    summary="List the authenticated adult's own relationship requests",
)
async def api_list_my_relationship_requests(
    org_uuid: str,
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> list[StudyRelationshipRead]:
    organization = await _organization(org_uuid, db_session)
    return await list_my_relationship_requests(db_session, organization, current_user)


@router.post(
    "/orgs/{org_uuid}/learners/{learner_uuid}/relationships/{relationship_uuid}/withdrawals",
    response_model=StudyRelationshipRead,
    summary="Withdraw the authenticated adult's own pending guardian-authority request",
)
async def api_withdraw_my_relationship_request(
    org_uuid: str,
    learner_uuid: str,
    relationship_uuid: str,
    request: StudyRelationshipWithdrawalCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyRelationshipRead:
    organization = await _organization(org_uuid, db_session)
    return await withdraw_my_relationship_request(
        db_session,
        organization,
        current_user,
        learner_uuid,
        relationship_uuid,
        request,
        idempotency_key,
    )


@router.get(
    "/orgs/{org_uuid}/family-review-queue",
    response_model=list[StudyFamilyReviewQueueItem],
    summary="List relationship reviews within the authenticated staffer's assigned learner scopes",
)
async def api_list_family_review_queue(
    org_uuid: str,
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> list[StudyFamilyReviewQueueItem]:
    organization = await _organization(org_uuid, db_session)
    return await list_family_review_queue(db_session, organization, current_user)


@router.get(
    "/orgs/{org_uuid}/learners/{learner_uuid}/relationships/{relationship_uuid}",
    response_model=StudyRelationshipReviewRead,
    summary="Read relationship evidence within an assigned family-services scope",
)
async def api_get_relationship_for_review(
    org_uuid: str,
    learner_uuid: str,
    relationship_uuid: str,
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyRelationshipReviewRead:
    organization = await _organization(org_uuid, db_session)
    return await get_relationship_for_review(
        db_session,
        organization,
        current_user,
        learner_uuid,
        relationship_uuid,
    )


@router.post(
    "/orgs/{org_uuid}/learners/{learner_uuid}/relationships/{relationship_uuid}/decisions",
    response_model=StudyRelationshipRead,
    status_code=status.HTTP_200_OK,
    summary="Record an assigned, version-checked guardian-authority decision",
)
async def api_decide_relationship_request(
    org_uuid: str,
    learner_uuid: str,
    relationship_uuid: str,
    request: StudyRelationshipDecisionCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyRelationshipRead:
    organization = await _organization(org_uuid, db_session)
    return await decide_relationship_request(
        db_session,
        organization,
        current_user,
        learner_uuid,
        relationship_uuid,
        request,
        idempotency_key,
    )


@router.post(
    "/orgs/{org_uuid}/learners/{learner_uuid}/relationships/{relationship_uuid}/restrictions",
    response_model=StudyRelationshipRead,
    summary="Restrict, dispute or revoke one learner relationship",
)
async def api_restrict_relationship_authority(
    org_uuid: str,
    learner_uuid: str,
    relationship_uuid: str,
    request: StudyRelationshipRestrictionCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudyRelationshipRead:
    organization = await _organization(org_uuid, db_session)
    return await restrict_relationship_authority(
        db_session,
        organization,
        current_user,
        learner_uuid,
        relationship_uuid,
        request,
        idempotency_key,
    )


@router.get(
    "/orgs/{org_uuid}/my-sharing-grants",
    response_model=list[StudySharingGrantRead],
    summary="Read the authenticated learner's own sharing choices",
)
async def api_list_my_sharing_grants(
    org_uuid: str,
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> list[StudySharingGrantRead]:
    organization = await _organization(org_uuid, db_session)
    return await list_my_sharing_grants(db_session, organization, current_user)


@router.post(
    "/orgs/{org_uuid}/my-sharing-grants",
    response_model=StudySharingGrantRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a learner-controlled, time-bounded sharing choice",
)
async def api_create_my_sharing_grant(
    org_uuid: str,
    request: StudySharingGrantCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudySharingGrantRead:
    organization = await _organization(org_uuid, db_session)
    return await create_my_sharing_grant(
        db_session,
        organization,
        current_user,
        request,
        idempotency_key,
    )


@router.delete(
    "/orgs/{org_uuid}/my-sharing-grants/{grant_uuid}",
    response_model=StudySharingGrantRead,
    summary="Withdraw the authenticated learner's own sharing choice",
)
async def api_withdraw_my_sharing_grant(
    org_uuid: str,
    grant_uuid: str,
    request: StudySharingGrantWithdrawal,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    current_user: PublicUser = Depends(get_authenticated_non_api_token_user),
    db_session: AsyncSession = Depends(get_db_session),
) -> StudySharingGrantRead:
    organization = await _organization(org_uuid, db_session)
    return await withdraw_my_sharing_grant(
        db_session,
        organization,
        current_user,
        grant_uuid,
        request.expected_version,
        idempotency_key,
    )
