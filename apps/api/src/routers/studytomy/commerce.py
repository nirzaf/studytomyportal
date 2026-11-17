"""Session-authenticated checkout for explicitly approved pilot offers."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.studytomy.collections import (
    StudyCollectionRemedy,
    StudyCollectionResolution,
)
from src.db.users import PublicUser
from src.routers.studytomy.private_route import PrivateStudyTomyRoute
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.security.org_auth import require_org_membership
from src.services.studytomy.access_policy import (
    ManagedAccessHistory,
    ManagedLessonIndex,
    ManagedReleaseCatalog,
    ManagedReleaseOverview,
    ManagedTextLesson,
    list_managed_text_lessons,
    list_my_managed_access_history,
    list_my_managed_releases,
    read_managed_access_history,
    read_managed_activity_media,
    read_managed_release_overview,
    read_managed_text_lesson,
)
from src.services.studytomy.allocations import (
    AllocationCorrectionCommand,
    AllocationCorrectionReceipt,
    AllocationCorrectionReview,
    correct_unstarted_allocation,
    read_allocation_correction_review,
)
from src.services.studytomy.collections import (
    FinanceReviewPage,
    ManualBankEvidence,
    ManualRemedyResolution,
    allocate_approved_free_order,
    allocate_confirmed_order,
    attest_manual_bank_evidence,
    confirm_manual_collection,
    list_manual_collection_reviews,
    record_manual_collection_attempt,
    resolve_manual_collection_remedy,
)
from src.services.studytomy.orders import (
    NamedOrderQuote,
    accept_named_order,
    preview_named_order,
    record_named_order_draft,
    require_pilot_checkout,
)
from src.services.studytomy.policies import lock_organization
from src.services.studytomy.receipts import (
    OrderReceiptView,
    PurchasePage,
    list_my_purchases,
    read_my_order_receipt,
)
from src.services.studytomy.refunds import (
    ChargebackEvidence,
    ChargebackReceipt,
    ChargebackReversalEvidence,
    ChargebackReversalReceipt,
    CollectionOverlapAcknowledgement,
    CollectionOverlapReview,
    PurchaserRefundPage,
    RefundRequestOptions,
    RefundRequestReceipt,
    RefundRequestReview,
    RefundReservationReceipt,
    RefundReservationRequest,
    RefundSettlementEvidence,
    RefundSettlementReceipt,
    attest_chargeback_reversal,
    attest_confirmed_chargeback,
    attest_refund_settlement,
    list_purchaser_refund_status,
    read_purchaser_refund_options,
    read_refund_request_review,
    record_purchaser_refund_request,
    reserve_manual_refund,
    review_collection_overlap,
)

router = APIRouter(prefix="/orgs/{org_uuid}", route_class=PrivateStudyTomyRoute)

Session = Annotated[AsyncSession, Depends(get_db_session)]
Purchaser = Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)]


class CheckoutSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offer_uuid: UUID
    offer_version_uuid: UUID
    learner_uuids: list[UUID] = Field(min_length=1, max_length=100)


class CheckoutConfirmation(CheckoutSelection):
    confirmation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    acknowledged_overlap: StrictBool


class CheckoutReceipt(BaseModel):
    customer_ref: str


class CollectionReceipt(BaseModel):
    attempt_reference: UUID
    status: Literal["pending", "evidence_attested", "confirmed", "remedy_review", "resolved"]


class AllocationReceipt(BaseModel):
    attempt_reference: UUID
    grant_count: int


async def _member_org(org_uuid: str, actor: PublicUser, db: AsyncSession) -> Organization:
    org = (await db.execute(select(Organization).where(
        Organization.org_uuid == org_uuid,
    ))).scalar_one_or_none()
    if org is None:
        raise HTTPException(404, "Organization not found")
    await require_org_membership(actor.id, org.id, db)
    return org


@router.post("/checkout/quote")
async def quote_order(
    org_uuid: str, selection: CheckoutSelection, response: Response, actor: Purchaser, db: Session,
) -> NamedOrderQuote:
    org = await _member_org(org_uuid, actor, db)
    request = selection.model_dump(mode="json")
    await require_pilot_checkout(db, org, offer_uuid=request["offer_uuid"], offer_version_uuid=request["offer_version_uuid"])
    quote = await preview_named_order(db, org, purchaser_id=actor.id, **request)
    response.headers["Cache-Control"] = "private, no-store"
    return quote


@router.post("/checkout/orders", status_code=201)
async def draft_order(
    org_uuid: str, confirmation: CheckoutConfirmation, response: Response, actor: Purchaser, db: Session,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> CheckoutReceipt:
    org = await _member_org(org_uuid, actor, db)
    await lock_organization(db, org.id)
    request = confirmation.model_dump(mode="json")
    await require_pilot_checkout(db, org, offer_uuid=request["offer_uuid"], offer_version_uuid=request["offer_version_uuid"])
    reference = await record_named_order_draft(
        db, org, purchaser_id=actor.id, idempotency_key=idempotency_key, **request,
    )
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return CheckoutReceipt(customer_ref=reference)


@router.post("/checkout/orders/{customer_ref}/accept")
async def accept_order(
    org_uuid: str, customer_ref: UUID, confirmation: CheckoutConfirmation,
    response: Response, actor: Purchaser, db: Session,
) -> CheckoutReceipt:
    org = await _member_org(org_uuid, actor, db)
    acceptance = await accept_named_order(
        db, org, purchaser_id=actor.id, customer_ref=str(customer_ref),
        **confirmation.model_dump(mode="json"),
    )
    await allocate_approved_free_order(db, org, acceptance)
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return CheckoutReceipt(customer_ref=str(customer_ref))


@router.get("/checkout/orders/{customer_ref}/receipt")
async def get_order_receipt(
    org_uuid: str, customer_ref: UUID, response: Response, actor: Purchaser, db: Session,
) -> OrderReceiptView:
    org = await _member_org(org_uuid, actor, db)
    receipt = await read_my_order_receipt(db, org, actor.id, customer_ref)
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.get("/purchases")
async def get_my_purchases(
    org_uuid: str, response: Response, actor: Purchaser, db: Session,
    after: UUID | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> PurchasePage:
    org = await _member_org(org_uuid, actor, db)
    purchases = await list_my_purchases(db, org, actor.id, after=after, limit=limit)
    response.headers["Cache-Control"] = "private, no-store"
    return purchases


_COLLECTION_PATH = "/collections/{customer_ref}/manual-attempts/{attempt_reference}"


@router.get("/learners/{learner_uuid}/allocations/{customer_ref}/correction-review")
async def get_allocation_correction_review(
    org_uuid: str, learner_uuid: Annotated[str, Path(min_length=1, max_length=100)],
    customer_ref: UUID, response: Response, actor: Purchaser, db: Session,
) -> AllocationCorrectionReview:
    org = await _member_org(org_uuid, actor, db)
    review = await read_allocation_correction_review(db, org, actor, learner_uuid, customer_ref)
    response.headers["Cache-Control"] = "private, no-store"
    return review


@router.put("/learners/{learner_uuid}/allocations/{customer_ref}/correction")
async def put_allocation_correction(
    org_uuid: str, learner_uuid: Annotated[str, Path(min_length=1, max_length=100)],
    customer_ref: UUID, command: AllocationCorrectionCommand, response: Response, actor: Purchaser, db: Session,
) -> AllocationCorrectionReceipt:
    org = await _member_org(org_uuid, actor, db)
    receipt = await correct_unstarted_allocation(db, org, actor, learner_uuid, customer_ref, command)
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.get("/collections/reviews")
async def get_manual_reviews(
    org_uuid: str, response: Response, actor: Purchaser, db: Session,
    after: UUID | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> FinanceReviewPage:
    org = await _member_org(org_uuid, actor, db)
    result = await list_manual_collection_reviews(db, org, actor, after=after, limit=limit)
    response.headers["Cache-Control"] = "private, no-store"
    return result


@router.get("/learning/access-history")
async def get_my_managed_access_history(
    org_uuid: str, response: Response, actor: Purchaser, db: Session,
    after: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ManagedReleaseCatalog:
    org = await _member_org(org_uuid, actor, db)
    history = await list_my_managed_access_history(db, org, actor, after=after, limit=limit)
    response.headers["Cache-Control"] = "private, no-store"
    return history


@router.get("/learning/releases")
async def get_my_managed_releases(
    org_uuid: str, response: Response, actor: Purchaser, db: Session,
    after: Annotated[str | None, Query(min_length=1, max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> ManagedReleaseCatalog:
    org = await _member_org(org_uuid, actor, db)
    catalog = await list_my_managed_releases(db, org, actor, after=after, limit=limit)
    response.headers["Cache-Control"] = "private, no-store"
    return catalog


@router.get("/learning/courses/{course_uuid}/releases/{release_uuid}")
async def get_managed_release_overview(
    org_uuid: str, course_uuid: str, release_uuid: str,
    response: Response, actor: Purchaser, db: Session,
) -> ManagedReleaseOverview:
    org = await _member_org(org_uuid, actor, db)
    overview = await read_managed_release_overview(db, org, actor, course_uuid, release_uuid)
    response.headers["Cache-Control"] = "private, no-store"
    return overview


@router.get("/learning/courses/{course_uuid}/releases/{release_uuid}/access-history")
async def get_managed_access_history(
    org_uuid: str, course_uuid: str, release_uuid: str,
    response: Response, actor: Purchaser, db: Session,
) -> ManagedAccessHistory:
    org = await _member_org(org_uuid, actor, db)
    history = await read_managed_access_history(db, org, actor, course_uuid, release_uuid)
    response.headers["Cache-Control"] = "private, no-store"
    return history


@router.get("/learning/courses/{course_uuid}/releases/{release_uuid}/lessons")
async def get_managed_lesson_index(
    org_uuid: str, course_uuid: str, release_uuid: str,
    response: Response, actor: Purchaser, db: Session,
) -> ManagedLessonIndex:
    org = await _member_org(org_uuid, actor, db)
    index = await list_managed_text_lessons(db, org, actor, course_uuid, release_uuid)
    response.headers["Cache-Control"] = "private, no-store"
    return index


@router.get("/learning/courses/{course_uuid}/releases/{release_uuid}/lessons/{activity_uuid}")
async def get_managed_text_lesson(
    org_uuid: str, course_uuid: str, release_uuid: str, activity_uuid: str,
    response: Response, actor: Purchaser, db: Session,
) -> ManagedTextLesson:
    org = await _member_org(org_uuid, actor, db)
    lesson = await read_managed_text_lesson(db, org, actor, course_uuid, release_uuid, activity_uuid)
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return lesson


@router.post("/learning/courses/{course_uuid}/releases/{release_uuid}/lessons/{activity_uuid}/open")
async def open_managed_text_lesson(
    org_uuid: str, course_uuid: str, release_uuid: str, activity_uuid: str,
    response: Response, actor: Purchaser, db: Session,
) -> ManagedTextLesson:
    org = await _member_org(org_uuid, actor, db)
    lesson = await read_managed_text_lesson(
        db, org, actor, course_uuid, release_uuid, activity_uuid, start_first_use=True,
    )
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return lesson


async def _managed_activity_media_response(
    org_uuid: str,
    course_uuid: str,
    release_uuid: str,
    activity_uuid: str,
    request: Request,
    actor: Purchaser,
    db: Session,
    *,
    start_first_use: bool,
) -> StreamingResponse:
    org = await _member_org(org_uuid, actor, db)
    media = await read_managed_activity_media(
        db,
        org,
        actor,
        course_uuid,
        release_uuid,
        activity_uuid,
        range_header=request.headers.get("range"),
        start_first_use=start_first_use,
    )
    try:
        await db.commit()
    except Exception:
        await media.close()
        raise
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, no-store",
        "Content-Disposition": f'inline; filename="{media.filename}"',
        "Content-Length": str(media.end - media.start + 1),
        "X-Content-Type-Options": "nosniff",
    }
    if media.status_code == 206:
        headers["Content-Range"] = (
            f"bytes {media.start}-{media.end}/{media.size_bytes}"
        )
    return StreamingResponse(
        media.stream(),
        status_code=media.status_code,
        media_type=media.media_type,
        headers=headers,
    )


@router.get(
    "/learning/courses/{course_uuid}/releases/{release_uuid}/activities/{activity_uuid}/media",
    response_class=StreamingResponse,
)
async def get_managed_activity_media(
    org_uuid: str,
    course_uuid: str,
    release_uuid: str,
    activity_uuid: str,
    request: Request,
    actor: Purchaser,
    db: Session,
) -> StreamingResponse:
    return await _managed_activity_media_response(
        org_uuid,
        course_uuid,
        release_uuid,
        activity_uuid,
        request,
        actor,
        db,
        start_first_use=False,
    )


@router.post(
    "/learning/courses/{course_uuid}/releases/{release_uuid}/activities/{activity_uuid}/media/open",
    response_class=StreamingResponse,
)
async def open_managed_activity_media(
    org_uuid: str,
    course_uuid: str,
    release_uuid: str,
    activity_uuid: str,
    request: Request,
    actor: Purchaser,
    db: Session,
) -> StreamingResponse:
    return await _managed_activity_media_response(
        org_uuid,
        course_uuid,
        release_uuid,
        activity_uuid,
        request,
        actor,
        db,
        start_first_use=True,
    )


@router.put(_COLLECTION_PATH)
async def put_manual_attempt(
    org_uuid: str, customer_ref: UUID, attempt_reference: UUID,
    response: Response, actor: Purchaser, db: Session,
) -> CollectionReceipt:
    org = await _member_org(org_uuid, actor, db)
    await record_manual_collection_attempt(
        db, org, actor, customer_ref=customer_ref, attempt_reference=attempt_reference,
    )
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return CollectionReceipt(attempt_reference=attempt_reference, status="pending")


@router.put(_COLLECTION_PATH + "/bank-evidence")
async def put_manual_evidence(
    org_uuid: str, customer_ref: UUID, attempt_reference: UUID, command: ManualBankEvidence,
    response: Response, actor: Purchaser, db: Session,
) -> CollectionReceipt:
    org = await _member_org(org_uuid, actor, db)
    await attest_manual_bank_evidence(
        db, org, actor, customer_ref=customer_ref, attempt_reference=attempt_reference, command=command,
    )
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return CollectionReceipt(attempt_reference=attempt_reference, status="evidence_attested")


@router.put(_COLLECTION_PATH + "/confirmation")
async def put_manual_confirmation(
    org_uuid: str, customer_ref: UUID, attempt_reference: UUID,
    response: Response, actor: Purchaser, db: Session,
) -> CollectionReceipt:
    org = await _member_org(org_uuid, actor, db)
    result = await confirm_manual_collection(
        db, org, actor, customer_ref=customer_ref, attempt_reference=attempt_reference,
    )
    resolved = isinstance(result, StudyCollectionRemedy) and (
        await db.get(StudyCollectionResolution, result.attempt_id) is not None
    )
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    if resolved:
        return CollectionReceipt(attempt_reference=attempt_reference, status="resolved")
    if isinstance(result, StudyCollectionRemedy):
        response.status_code = 202
        return CollectionReceipt(attempt_reference=attempt_reference, status="remedy_review")
    return CollectionReceipt(attempt_reference=attempt_reference, status="confirmed")


@router.put(_COLLECTION_PATH + "/resolution")
async def put_manual_resolution(
    org_uuid: str, customer_ref: UUID, attempt_reference: UUID, command: ManualRemedyResolution,
    response: Response, actor: Purchaser, db: Session,
) -> CollectionReceipt:
    org = await _member_org(org_uuid, actor, db)
    await resolve_manual_collection_remedy(
        db, org, actor, customer_ref=customer_ref, attempt_reference=attempt_reference, command=command,
    )
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return CollectionReceipt(attempt_reference=attempt_reference, status="resolved")


@router.put(_COLLECTION_PATH + "/allocation")
async def put_manual_allocation(
    org_uuid: str, customer_ref: UUID, attempt_reference: UUID,
    response: Response, actor: Purchaser, db: Session,
) -> AllocationReceipt:
    org = await _member_org(org_uuid, actor, db)
    grants = await allocate_confirmed_order(
        db, org, actor, customer_ref=customer_ref, attempt_reference=attempt_reference,
    )
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return AllocationReceipt(attempt_reference=attempt_reference, grant_count=len(grants))


@router.get("/collections/{customer_ref}/refund-requests/{refund_reference}")
async def get_refund_request_review(
    org_uuid: str, customer_ref: UUID, refund_reference: UUID,
    response: Response, actor: Purchaser, db: Session,
) -> RefundRequestReview:
    org = await _member_org(org_uuid, actor, db)
    review = await read_refund_request_review(db, org, actor, customer_ref=customer_ref, refund_reference=refund_reference)
    response.headers["Cache-Control"] = "private, no-store"
    return review


@router.get("/checkout/orders/{customer_ref}/refund-options")
async def get_purchaser_refund_options(
    org_uuid: str, customer_ref: UUID, response: Response, actor: Purchaser, db: Session,
) -> RefundRequestOptions:
    org = await _member_org(org_uuid, actor, db)
    options = await read_purchaser_refund_options(db, org, actor, customer_ref)
    response.headers["Cache-Control"] = "private, no-store"
    return options


@router.get("/checkout/orders/{customer_ref}/refund-requests")
async def get_purchaser_refund_status(
    org_uuid: str, customer_ref: UUID, response: Response, actor: Purchaser, db: Session,
    after: UUID | None = None, limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> PurchaserRefundPage:
    org = await _member_org(org_uuid, actor, db)
    status = await list_purchaser_refund_status(db, org, actor, customer_ref, after=after, limit=limit)
    response.headers["Cache-Control"] = "private, no-store"
    return status


@router.put("/checkout/orders/{customer_ref}/refund-requests/{refund_reference}")
async def put_purchaser_refund_request(
    org_uuid: str, customer_ref: UUID, refund_reference: UUID, command: RefundReservationRequest,
    response: Response, actor: Purchaser, db: Session,
) -> RefundRequestReceipt:
    org = await _member_org(org_uuid, actor, db)
    receipt = await record_purchaser_refund_request(
        db, org, actor, customer_ref=customer_ref, refund_reference=refund_reference, command=command,
    )
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.put(_COLLECTION_PATH + "/refund-reservations/{refund_reference}")
async def put_refund_reservation(
    org_uuid: str, customer_ref: UUID, attempt_reference: UUID, refund_reference: UUID,
    command: RefundReservationRequest, response: Response, actor: Purchaser, db: Session,
) -> RefundReservationReceipt:
    org = await _member_org(org_uuid, actor, db)
    receipt = await reserve_manual_refund(
        db, org, actor, customer_ref=customer_ref, attempt_reference=attempt_reference,
        refund_reference=refund_reference, command=command,
    )
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.put(_COLLECTION_PATH + "/refund-reservations/{refund_reference}/settlement")
async def put_refund_settlement(
    org_uuid: str, customer_ref: UUID, attempt_reference: UUID, refund_reference: UUID,
    command: RefundSettlementEvidence, response: Response, actor: Purchaser, db: Session,
) -> RefundSettlementReceipt:
    org = await _member_org(org_uuid, actor, db)
    receipt = await attest_refund_settlement(
        db, org, actor, customer_ref=customer_ref, attempt_reference=attempt_reference,
        refund_reference=refund_reference, command=command,
    )
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.put(_COLLECTION_PATH + "/chargeback")
async def put_chargeback(
    org_uuid: str, customer_ref: UUID, attempt_reference: UUID,
    command: ChargebackEvidence, response: Response, actor: Purchaser, db: Session,
) -> ChargebackReceipt:
    org = await _member_org(org_uuid, actor, db)
    receipt = await attest_confirmed_chargeback(
        db, org, actor, customer_ref=customer_ref, attempt_reference=attempt_reference, command=command,
    )
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.get(_COLLECTION_PATH + "/overlap-review")
async def get_overlap_review(
    org_uuid: str, customer_ref: UUID, attempt_reference: UUID,
    response: Response, actor: Purchaser, db: Session,
) -> CollectionOverlapReview:
    org = await _member_org(org_uuid, actor, db)
    review = await review_collection_overlap(
        db, org, actor, customer_ref=customer_ref, attempt_reference=attempt_reference,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return review


@router.put(_COLLECTION_PATH + "/chargeback-reversal")
async def put_chargeback_reversal(
    org_uuid: str, customer_ref: UUID, attempt_reference: UUID,
    command: ChargebackReversalEvidence, response: Response, actor: Purchaser, db: Session,
) -> ChargebackReversalReceipt:
    org = await _member_org(org_uuid, actor, db)
    receipt = await attest_chargeback_reversal(
        db, org, actor, customer_ref=customer_ref, attempt_reference=attempt_reference, command=command,
    )
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.put(_COLLECTION_PATH + "/overlap-review")
async def put_overlap_review(
    org_uuid: str, customer_ref: UUID, attempt_reference: UUID,
    command: CollectionOverlapAcknowledgement, response: Response, actor: Purchaser, db: Session,
) -> CollectionOverlapReview:
    org = await _member_org(org_uuid, actor, db)
    review = await review_collection_overlap(
        db, org, actor, customer_ref=customer_ref, attempt_reference=attempt_reference,
        acknowledgement=command,
    )
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return review
