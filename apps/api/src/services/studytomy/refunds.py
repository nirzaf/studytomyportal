"""Reserve approved refunds against retained collection evidence."""

import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import and_, func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.access import StudyCourseAccessGrant
from src.db.studytomy.collections import (
    StudyChargebackAdjustment,
    StudyChargebackReversal,
    StudyMoneyMovement,
    StudyPaymentAttempt,
    StudyRefundReservation,
    StudyRefundSettlement,
)
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyPolicyKey,
    StudyPolicyVersion,
    inspect_study_policy_body,
)
from src.db.studytomy.orders import StudyOrder, StudyOrderLine
from src.db.users import PublicUser, User
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_financial_delegation,
    authorize_studytomy_staff_action,
)
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.collections import (
    _manual_attempt,
    _manual_collection_context,
)
from src.services.studytomy.concurrency import acquire_write_locks, register_lock_target
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization
from src.services.studytomy.receipts import require_purchase_receipt_scope

register_lock_target("collection", StudyMoneyMovement)
register_lock_target("refund_reservation", StudyRefundReservation)
register_lock_target("entitlement", StudyCourseAccessGrant)
_REASON_CODE = r"^[a-z][a-z0-9_]{0,63}$"


class RefundReservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_minor: int = Field(strict=True, gt=0, le=9_000_000_000_000_000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    reason_code: str = Field(pattern=_REASON_CODE)


class RefundReservationReceipt(BaseModel):
    refund_reference: UUID
    amount_minor: int
    currency: str
    status: Literal["awaiting_second_approval", "reserved"] = "reserved"


class RefundRequestReceipt(BaseModel):
    refund_reference: UUID
    status: Literal["requested"] = "requested"


RefundStatus = Literal["requested", "reserved", "settled"]


def _refund_status(reservation_id: int | None, settlement_id: int | None) -> RefundStatus:
    if settlement_id is not None:
        return "settled"
    return "reserved" if reservation_id is not None else "requested"


class PurchaserRefundStatus(BaseModel):
    refund_reference: UUID
    status: RefundStatus


class PurchaserRefundPage(BaseModel):
    requests: list[PurchaserRefundStatus]
    next_cursor: UUID | None


class RefundRequestReview(RefundReservationRequest):
    refund_reference: UUID
    attempt_reference: UUID
    status: RefundStatus


class RefundRequestOptions(BaseModel):
    currency: str
    maximum_minor: int
    reason_codes: list[str]


class RefundSettlementEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    amount_minor: int = Field(strict=True, gt=0, le=9_000_000_000_000_000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    external_reference: str = Field(min_length=1, max_length=150)
    evidence_reference: str = Field(min_length=1, max_length=150)
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    settled_at: AwareDatetime


class RefundSettlementReceipt(BaseModel):
    refund_reference: UUID
    status: Literal["settled"] = "settled"
    review_required: bool = False


class ChargebackEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    amount_minor: int = Field(strict=True, gt=0, le=9_000_000_000_000_000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    external_reference: str = Field(min_length=1, max_length=150)
    case_reference: str = Field(min_length=1, max_length=150)
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effective_at: AwareDatetime


class ChargebackReceipt(BaseModel):
    status: Literal["recorded"] = "recorded"
    review_required: bool


class ChargebackReversalReceipt(BaseModel):
    status: Literal["reversed"] = "reversed"


class ChargebackReversalEvidence(ChargebackEvidence):
    debit_reference: str = Field(min_length=1, max_length=150)


class CollectionOverlapReview(BaseModel):
    currency: str
    confirmed_minor: int
    reserved_minor: int
    settled_minor: int
    chargeback_minor: int
    review_required: bool
    acknowledged: bool
    snapshot_sha256: str


class CollectionOverlapAcknowledgement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


async def review_collection_overlap(
    db: AsyncSession, org: Organization, actor: PublicUser, *,
    customer_ref: UUID, attempt_reference: UUID, acknowledgement: CollectionOverlapAcknowledgement | None = None,
) -> CollectionOverlapReview:
    order, finance = await _manual_collection_context(db, org, actor, customer_ref)
    attempt = await _manual_attempt(db, org.id, order.id, attempt_reference)
    movement = await _confirmed_chargeback_collection(db, org.id, order.id, attempt.id)
    (remedy,) = await _current_policies(db, org, (StudyPolicyKey.D_07,), datetime.now(UTC))
    if acknowledgement is not None:
        body, _ = inspect_study_policy_body(StudyPolicyKey.D_07, remedy.body)
        disputes = body.specified_value("disputes") if body else None
        if not isinstance(disputes, dict) or disputes.get("manual_overlap_acknowledgement_enabled") is not True:
            raise studytomy_http_error(409, "OVERLAP_REVIEW_NOT_APPROVED", "Current policy does not approve this review action.")
    await acquire_write_locks(db, [("collection", movement.id)])
    reservation = StudyRefundReservation
    settlement = StudyRefundSettlement
    reservations = (await db.execute(
        select(reservation.id, reservation.amount_minor, settlement.reservation_id)
        .outerjoin(settlement, settlement.reservation_id == reservation.id)
        .where(reservation.org_id == org.id, reservation.movement_id == movement.id)
        .order_by(reservation.id)
    )).all()
    adjustment = StudyChargebackAdjustment
    reversal = StudyChargebackReversal
    debits = (await db.execute(
        select(adjustment.id, adjustment.amount_minor, reversal.adjustment_id)
        .outerjoin(reversal, reversal.adjustment_id == adjustment.id)
        .where(adjustment.org_id == org.id, adjustment.movement_id == movement.id)
        .order_by(adjustment.id)
    )).all()
    reserved = sum(amount for _, amount, settled_id in reservations if settled_id is None)
    settled = sum(amount for _, amount, settled_id in reservations if settled_id is not None)
    charged_back = sum(amount for _, amount, reversed_id in debits if reversed_id is None)
    overlap = reserved + settled + charged_back > movement.amount_minor
    digest = canonical_request_hash({
        "movement_id": movement.id, "reservations": [tuple(row) for row in reservations],
        "chargebacks": [tuple(row) for row in debits], "policies": [remedy.id, finance.id],
    })
    key = f"studytomy:chargeback:review:{org.id}:{movement.id}:{digest}"
    existing = (await db.execute(select(StudyBusinessEvent.id).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.dedupe_key == key,
    ))).scalar_one_or_none()
    if acknowledgement is not None:
        if acknowledgement.snapshot_sha256 != digest or not overlap:
            raise studytomy_http_error(409, "OVERLAP_REVIEW_STALE", "Reload the current overlap before acknowledging it.")
        if existing is None:
            await append_business_event(
                db, org_id=org.id, actor_user_id=actor.id, subject_uuid=str(customer_ref),
                resource_kind="collection_attempt", resource_uuid=str(attempt_reference),
                action="studytomy.chargeback.review_acknowledged", reason_code="COLLECTION_OVERLAP_ACKNOWLEDGED",
                policy_refs=[remedy.id, finance.id], after_state={
                    "status": "acknowledged", "source_snapshot_digest": digest,
                }, dedupe_key=key,
            )
    return CollectionOverlapReview(
        currency=movement.currency, confirmed_minor=movement.amount_minor,
        reserved_minor=reserved, settled_minor=settled, chargeback_minor=charged_back,
        review_required=overlap, acknowledged=existing is not None or acknowledgement is not None,
        snapshot_sha256=digest,
    )


async def _confirmed_chargeback_collection(
    db: AsyncSession, org_id: int, order_id: int, attempt_id: int,
) -> StudyMoneyMovement:
    movement = (await db.execute(select(StudyMoneyMovement).where(
        StudyMoneyMovement.org_id == org_id, StudyMoneyMovement.order_id == order_id,
        StudyMoneyMovement.attempt_id == attempt_id,
    ))).scalar_one_or_none()
    if movement is None:
        raise studytomy_http_error(404, "CHARGEBACK_COLLECTION_UNAVAILABLE", "The confirmed collection is unavailable.")
    return movement


async def attest_confirmed_chargeback(
    db: AsyncSession, org: Organization, actor: PublicUser, *,
    customer_ref: UUID, attempt_reference: UUID, command: ChargebackEvidence,
) -> ChargebackReceipt:
    order, finance = await _manual_collection_context(db, org, actor, customer_ref)
    attempt = await _manual_attempt(db, org.id, order.id, attempt_reference)
    movement = await _confirmed_chargeback_collection(db, org.id, order.id, attempt.id)
    now = datetime.now(UTC)
    (remedy,) = await _current_policies(db, org, (StudyPolicyKey.D_07,), now)
    remedy_body, _ = inspect_study_policy_body(StudyPolicyKey.D_07, remedy.body)
    finance_body, _ = inspect_study_policy_body(StudyPolicyKey.D_19, finance.body)
    disputes = remedy_body.specified_value("disputes") if remedy_body else None
    authority = finance_body.specified_value("staff_authority") if finance_body else None
    if (
        not isinstance(disputes, dict) or disputes.get("manual_chargeback_access_effect") != "preserve"
        or not isinstance(authority, dict) or authority.get("manual_chargeback_attestation_enabled") is not True
    ):
        raise studytomy_http_error(409, "CHARGEBACK_ATTESTATION_NOT_APPROVED", "Current policy does not approve this adjustment.")
    if command.currency != movement.currency or not movement.confirmed_at <= command.effective_at <= now:
        raise studytomy_http_error(409, "CHARGEBACK_EVIDENCE_MISMATCH", "The adjustment must match the collection currency and dates.")
    await acquire_write_locks(db, [("collection", movement.id)])
    adjustment = StudyChargebackAdjustment
    existing = (await db.execute(select(adjustment).where(
        adjustment.org_id == org.id, adjustment.external_reference == command.external_reference,
    ))).scalar_one_or_none()
    fields = command.model_dump()
    charges = await _chargeback_total(db, org.id, movement.id)
    if existing is not None:
        if existing.movement_id != movement.id or any(getattr(existing, name) != value for name, value in fields.items()):
            raise studytomy_http_error(409, "CHARGEBACK_REFERENCE_CONFLICT", "The debit reference already has different evidence.")
    else:
        charges += command.amount_minor
        if charges > movement.amount_minor:
            raise studytomy_http_error(409, "CHARGEBACK_AMOUNT_EXCEEDED", "Confirmed debits exceed the original collection.")
        db.add(adjustment(
            org_id=org.id, order_id=order.id, movement_id=movement.id, verified_by_user_id=actor.id,
            remedy_policy_id=remedy.id, finance_policy_id=finance.id, verified_at=now, **fields,
        ))
        await db.flush()
    reservations = (await db.execute(select(func.coalesce(func.sum(StudyRefundReservation.amount_minor), 0)).where(
        StudyRefundReservation.org_id == org.id, StudyRefundReservation.movement_id == movement.id,
    ))).scalar_one()
    overlap = charges + reservations > movement.amount_minor
    if existing is None:
        await append_business_event(
            db, org_id=org.id, actor_user_id=actor.id, subject_uuid=str(customer_ref),
            resource_kind="collection_attempt", resource_uuid=str(attempt_reference),
            action="studytomy.chargeback.recorded", reason_code="EXTERNAL_DEBIT_CONFIRMED",
            policy_refs=[remedy.id, finance.id], after_state={
                "status": "review_required" if overlap else "recorded",
                "source_snapshot_digest": command.record_sha256,
            },
            dedupe_key=f"studytomy:chargeback:{org.id}:{canonical_request_hash(command.external_reference)}",
        )
    return ChargebackReceipt(review_required=overlap)


async def attest_chargeback_reversal(
    db: AsyncSession, org: Organization, actor: PublicUser, *,
    customer_ref: UUID, attempt_reference: UUID, command: ChargebackReversalEvidence,
) -> ChargebackReversalReceipt:
    order, finance = await _manual_collection_context(db, org, actor, customer_ref)
    attempt = await _manual_attempt(db, org.id, order.id, attempt_reference)
    movement = await _confirmed_chargeback_collection(db, org.id, order.id, attempt.id)
    now = datetime.now(UTC)
    (remedy,) = await _current_policies(db, org, (StudyPolicyKey.D_07,), now)
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_07, remedy.body)
    finance_body, _ = inspect_study_policy_body(StudyPolicyKey.D_19, finance.body)
    disputes = body.specified_value("disputes") if body else None
    authority = finance_body.specified_value("staff_authority") if finance_body else None
    if (
        not isinstance(disputes, dict) or disputes.get("manual_chargeback_reversal_access_effect") != "preserve"
        or not isinstance(authority, dict) or authority.get("manual_chargeback_reversal_enabled") is not True
    ):
        raise studytomy_http_error(409, "CHARGEBACK_REVERSAL_NOT_APPROVED", "Current policy does not approve this reversal.")
    await acquire_write_locks(db, [("collection", movement.id)])
    debit = (await db.execute(select(StudyChargebackAdjustment).where(
        StudyChargebackAdjustment.org_id == org.id, StudyChargebackAdjustment.movement_id == movement.id,
        StudyChargebackAdjustment.external_reference == command.debit_reference,
    ))).scalar_one_or_none()
    if debit is None:
        raise studytomy_http_error(404, "CHARGEBACK_DEBIT_UNAVAILABLE", "The confirmed debit is unavailable.")
    if (
        command.amount_minor != debit.amount_minor or command.currency != debit.currency
        or command.case_reference != debit.case_reference or command.external_reference == debit.external_reference
        or not debit.effective_at <= command.effective_at <= now
    ):
        raise studytomy_http_error(409, "CHARGEBACK_REVERSAL_MISMATCH", "The reversal must match the original debit and case.")
    fields = command.model_dump(exclude={"amount_minor", "currency", "debit_reference"})
    reversal = StudyChargebackReversal
    existing = await db.get(reversal, debit.id)
    if existing is not None:
        if any(getattr(existing, name) != value for name, value in fields.items()):
            raise studytomy_http_error(409, "CHARGEBACK_REVERSAL_CONFLICT", "The debit already has different reversal evidence.")
        return ChargebackReversalReceipt()
    duplicate = (await db.execute(select(reversal.adjustment_id).where(
        reversal.org_id == org.id, reversal.external_reference == command.external_reference,
    ))).scalar_one_or_none()
    if duplicate is not None:
        raise studytomy_http_error(409, "CHARGEBACK_REVERSAL_REFERENCE_USED", "The reversal reference is already retained.")
    db.add(reversal(
        adjustment_id=debit.id, org_id=org.id, verified_by_user_id=actor.id,
        remedy_policy_id=remedy.id, finance_policy_id=finance.id, verified_at=now, **fields,
    ))
    await append_business_event(
        db, org_id=org.id, actor_user_id=actor.id, subject_uuid=str(customer_ref),
        resource_kind="collection_attempt", resource_uuid=str(attempt_reference),
        action="studytomy.chargeback.reversed", reason_code="EXTERNAL_DEBIT_REVERSED",
        policy_refs=[remedy.id, finance.id], after_state={"status": "reversed", "source_snapshot_digest": command.record_sha256},
        dedupe_key=f"studytomy:chargeback:reversed:{org.id}:{debit.id}",
    )
    return ChargebackReversalReceipt()


def _refund_request_digest(order: StudyOrder, movement: StudyMoneyMovement, command: RefundReservationRequest) -> str:
    return canonical_request_hash({
        "order_id": order.id, "movement_id": movement.id, "purchaser_id": order.purchaser_user_id,
        "request": command.model_dump(),
    })


def _request_event(org_id: int, refund_reference: UUID):
    return select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org_id,
        StudyBusinessEvent.dedupe_key == f"studytomy:refund:requested:{org_id}:{refund_reference}",
        StudyBusinessEvent.action == "studytomy.refund.requested",
    )


async def list_purchaser_refund_status(
    db: AsyncSession, org: Organization, actor: PublicUser, customer_ref: UUID, *,
    after: UUID | None, limit: int,
) -> PurchaserRefundPage:
    order, movement = await _purchaser_refund_collection(db, org, actor, customer_ref)
    await require_purchase_receipt_scope(db, org)
    event = StudyBusinessEvent
    reservation = StudyRefundReservation
    settlement = StudyRefundSettlement
    query = (
        select(event.id, event.resource_uuid, reservation.id, settlement.reservation_id)
        .outerjoin(reservation, and_(
            reservation.org_id == org.id, reservation.order_id == order.id, reservation.movement_id == movement.id,
            reservation.refund_reference == event.resource_uuid,
        ))
        .outerjoin(settlement, and_(settlement.org_id == org.id, settlement.reservation_id == reservation.id))
        .where(
            event.org_id == org.id, event.actor_user_id == actor.id,
            event.action == "studytomy.refund.requested", event.subject_uuid == str(customer_ref),
        )
    )
    if after is not None:
        pivot = (await db.execute(query.where(event.resource_uuid == str(after)))).one_or_none()
        if pivot is None:
            raise studytomy_http_error(404, "REFUND_REQUEST_UNAVAILABLE", "The refund request is unavailable.")
        query = query.where(event.id < pivot[0])
    rows = (await db.execute(query.order_by(event.id.desc()).limit(limit + 1))).all()
    entries = [
        PurchaserRefundStatus(refund_reference=reference, status=_refund_status(reserved, settled))
        for _, reference, reserved, settled in rows[:limit]
    ]
    return PurchaserRefundPage(
        requests=entries, next_cursor=entries[-1].refund_reference if len(rows) > limit else None,
    )


async def read_refund_request_review(
    db: AsyncSession, org: Organization, actor: PublicUser, *, customer_ref: UUID, refund_reference: UUID,
) -> RefundRequestReview:
    now = datetime.now(UTC)
    await authorize_studytomy_staff_action(
        db, actor, org, StudytomyCapabilityName.REFUND_APPROVAL,
        target_resource_uuid=str(customer_ref), target_scope_kind="order",
        hide_existence_on_denial=True, now=now,
    )
    await _current_policies(db, org, (StudyPolicyKey.D_07, StudyPolicyKey.D_19), now)
    missing = studytomy_http_error(404, "REFUND_REQUEST_UNAVAILABLE", "The refund request is unavailable.")
    event = (await db.execute(_request_event(org.id, refund_reference).where(
        StudyBusinessEvent.subject_uuid == str(customer_ref),
    ))).scalar_one_or_none()
    if event is None or event.after_state is None:
        raise missing
    try:
        command = RefundReservationRequest(
            amount_minor=event.after_state.get("refund_amount_minor"),
            currency=event.after_state.get("refund_currency"),
            reason_code=event.after_state.get("refund_reason_code"),
        )
    except ValidationError as error:
        raise missing from error
    rows = (await db.execute(
        select(StudyOrder, StudyMoneyMovement, StudyPaymentAttempt.attempt_reference)
        .join(StudyMoneyMovement, StudyMoneyMovement.order_id == StudyOrder.id)
        .join(StudyPaymentAttempt, StudyPaymentAttempt.id == StudyMoneyMovement.attempt_id)
        .where(
            StudyOrder.org_id == org.id, StudyOrder.customer_ref == str(customer_ref),
            StudyOrder.purchaser_user_id == event.actor_user_id, StudyMoneyMovement.org_id == org.id,
            StudyPaymentAttempt.method == "manual",
        ).limit(2)
    )).all()
    if len(rows) != 1:
        raise missing
    order, movement, attempt = rows[0]
    if event.after_state.get("source_snapshot_digest") != _refund_request_digest(order, movement, command):
        raise missing
    if actor.id == order.purchaser_user_id:
        raise missing
    reservation = StudyRefundReservation
    settlement = StudyRefundSettlement
    state = (await db.execute(
        select(reservation.id, settlement.reservation_id)
        .outerjoin(settlement, and_(settlement.org_id == org.id, settlement.reservation_id == reservation.id))
        .where(reservation.org_id == org.id, reservation.movement_id == movement.id,
               reservation.refund_reference == str(refund_reference))
    )).one_or_none()
    return RefundRequestReview(
        **command.model_dump(), refund_reference=refund_reference, attempt_reference=attempt,
        status=_refund_status(*state) if state is not None else "requested",
    )


async def _purchaser_refund_collection(
    db: AsyncSession, org: Organization, actor: PublicUser, customer_ref: UUID,
) -> tuple[StudyOrder, StudyMoneyMovement]:
    matches = (await db.execute(
        select(StudyOrder, StudyMoneyMovement)
        .join(StudyMoneyMovement, StudyMoneyMovement.order_id == StudyOrder.id)
        .join(StudyPaymentAttempt, StudyPaymentAttempt.id == StudyMoneyMovement.attempt_id)
        .where(
            StudyOrder.org_id == org.id, StudyOrder.customer_ref == str(customer_ref),
            StudyOrder.purchaser_user_id == actor.id, StudyMoneyMovement.org_id == org.id,
            StudyPaymentAttempt.method == "manual",
        ).limit(2)
    )).all()
    if len(matches) != 1:
        raise studytomy_http_error(404, "REFUND_COLLECTION_UNAVAILABLE", "The confirmed collection is unavailable.")
    order, movement = matches[0]
    return order, movement


def _manual_refund_reasons(remedy: StudyPolicyVersion, finance: StudyPolicyVersion) -> list[str]:
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_07, remedy.body)
    rules = body.specified_value("refunds") if body else None
    reasons = rules.get("manual_reservation_reasons") if isinstance(rules, dict) else None
    finance_body, _ = inspect_study_policy_body(StudyPolicyKey.D_19, finance.body)
    authority = finance_body.specified_value("staff_authority") if finance_body else None
    if (
        not isinstance(authority, dict) or authority.get("manual_refund_reservation_enabled") is not True
        or type(authority.get("refund_dual_approval_required")) is not bool
    ):
        return []
    if not isinstance(reasons, list):
        return []
    return sorted({reason for reason in reasons if isinstance(reason, str) and re.fullmatch(_REASON_CODE, reason)})


async def _retain_or_confirm_first_approval(
    db: AsyncSession, org: Organization, actor: PublicUser, order: StudyOrder,
    movement: StudyMoneyMovement, refund_reference: UUID, command: RefundReservationRequest,
    remedy: StudyPolicyVersion, finance: StudyPolicyVersion, now: datetime,
) -> StudyBusinessEvent | None:
    """Find another currently delegated approver's exact endorsement or retain the first."""
    event = StudyBusinessEvent
    policy_ids = [remedy.id, finance.id]
    digest = _refund_request_digest(order, movement, command)
    endorsements = (await db.execute(select(event).where(
        event.org_id == org.id, event.subject_uuid == order.customer_ref,
        event.resource_uuid == str(refund_reference), event.action == "studytomy.refund.first_approval",
    ).order_by(event.id))).scalars().all()
    already_endorsed = False
    for endorsement in endorsements:
        if endorsement.policy_refs != policy_ids or (endorsement.after_state or {}).get("source_snapshot_digest") != digest:
            continue
        if endorsement.actor_user_id == actor.id:
            already_endorsed = True
            continue
        if endorsement.actor_user_id is None:
            continue
        first_actor = await db.get(User, endorsement.actor_user_id)
        if first_actor is None:
            continue
        try:
            await authorize_financial_delegation(
                db, first_actor, org, amount_minor=command.amount_minor, currency=command.currency,
                reason=command.reason_code, target_order_uuid=order.customer_ref,
                requester_user_id=order.purchaser_user_id, beneficiary_user_id=order.purchaser_user_id, now=now,
            )
        except HTTPException as error:
            if error.status_code not in (401, 403, 404, 409):
                raise
            continue
        return endorsement
    if not already_endorsed:
        await append_business_event(
            db, org_id=org.id, actor_user_id=actor.id, subject_uuid=order.customer_ref,
            resource_kind="refund_request", resource_uuid=str(refund_reference),
            action="studytomy.refund.first_approval", reason_code="REFUND_FIRST_APPROVAL_RECORDED",
            policy_refs=policy_ids, after_state={"status": "awaiting_second_approval", "source_snapshot_digest": digest},
            dedupe_key=f"studytomy:refund:first:{org.id}:{refund_reference}:{remedy.id}:{finance.id}:{actor.id}",
        )
    return None


async def read_purchaser_refund_options(
    db: AsyncSession, org: Organization, actor: PublicUser, customer_ref: UUID,
) -> RefundRequestOptions:
    _, movement = await _purchaser_refund_collection(db, org, actor, customer_ref)
    try:
        remedy, finance = await _current_policies(
            db, org, (StudyPolicyKey.D_07, StudyPolicyKey.D_19), datetime.now(UTC),
        )
    except HTTPException as error:
        if error.status_code != 409:
            raise
        raise studytomy_http_error(404, "REFUND_OPTIONS_UNAVAILABLE", "Refund request options are unavailable.") from error
    reasons = _manual_refund_reasons(remedy, finance)
    if not reasons:
        raise studytomy_http_error(404, "REFUND_OPTIONS_UNAVAILABLE", "Refund request options are unavailable.")
    return RefundRequestOptions(currency=movement.currency, maximum_minor=movement.amount_minor, reason_codes=reasons)


async def record_purchaser_refund_request(
    db: AsyncSession, org: Organization, actor: PublicUser, *,
    customer_ref: UUID, refund_reference: UUID, command: RefundReservationRequest,
) -> RefundRequestReceipt:
    """Retain the authenticated purchaser's request without reserving any funds."""
    locked = await lock_organization(db, org.id)
    order, movement = await _purchaser_refund_collection(db, locked, actor, customer_ref)
    remedy, finance = await _current_policies(
        db, locked, (StudyPolicyKey.D_07, StudyPolicyKey.D_19), datetime.now(UTC),
    )
    if command.reason_code not in _manual_refund_reasons(remedy, finance):
        raise studytomy_http_error(409, "REFUND_REQUEST_NOT_APPROVED", "Current policy does not cover this request.")
    if command.currency != movement.currency or command.amount_minor > movement.amount_minor:
        raise studytomy_http_error(409, "REFUND_REQUEST_AMOUNT_UNAVAILABLE", "The amount or currency is unavailable for this collection.")
    digest = _refund_request_digest(order, movement, command)
    key = f"studytomy:refund:requested:{locked.id}:{refund_reference}"
    retained = (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == locked.id, StudyBusinessEvent.dedupe_key == key,
    ))).scalar_one_or_none()
    if retained is not None:
        if retained.actor_user_id != actor.id or (retained.after_state or {}).get("source_snapshot_digest") != digest:
            raise studytomy_http_error(409, "REFUND_REFERENCE_CONFLICT", "The reference already has a different request.")
    else:
        await append_business_event(
            db, org_id=locked.id, actor_user_id=actor.id, resource_kind="refund_request",
            subject_uuid=str(customer_ref),
            resource_uuid=str(refund_reference), action="studytomy.refund.requested",
            reason_code="PURCHASER_REFUND_REQUESTED", policy_refs=[remedy.id, finance.id],
            after_state={
                "status": "requested", "source_snapshot_digest": digest,
                "refund_amount_minor": command.amount_minor, "refund_currency": command.currency,
                "refund_reason_code": command.reason_code,
            }, dedupe_key=key,
        )
    return RefundRequestReceipt(refund_reference=refund_reference)


async def reserve_manual_refund(
    db: AsyncSession, org: Organization, actor: PublicUser, *,
    customer_ref: UUID, attempt_reference: UUID, refund_reference: UUID,
    command: RefundReservationRequest,
) -> RefundReservationReceipt:
    """Reserve funds for a referenced purchaser request inside the caller's transaction."""
    locked = await lock_organization(db, org.id)
    now = datetime.now(UTC)
    await authorize_studytomy_staff_action(
        db, actor, locked, StudytomyCapabilityName.REFUND_APPROVAL,
        target_resource_uuid=str(customer_ref), target_scope_kind="order",
        hide_existence_on_denial=True, now=now,
    )
    found = (await db.execute(
        select(StudyOrder, StudyMoneyMovement)
        .join(StudyMoneyMovement, StudyMoneyMovement.order_id == StudyOrder.id)
        .join(StudyPaymentAttempt, StudyPaymentAttempt.id == StudyMoneyMovement.attempt_id)
        .where(
            StudyOrder.org_id == locked.id, StudyOrder.customer_ref == str(customer_ref),
            StudyMoneyMovement.org_id == locked.id, StudyPaymentAttempt.attempt_reference == str(attempt_reference),
            StudyPaymentAttempt.method == "manual",
        )
    )).one_or_none()
    if found is None:
        raise studytomy_http_error(404, "REFUND_COLLECTION_UNAVAILABLE", "The confirmed collection is unavailable.")
    order, movement = found
    await authorize_financial_delegation(
        db, actor, locked, amount_minor=command.amount_minor, currency=command.currency,
        reason=command.reason_code, target_order_uuid=str(customer_ref),
        requester_user_id=order.purchaser_user_id, beneficiary_user_id=order.purchaser_user_id, now=now,
    )
    remedy, finance = await _current_policies(db, locked, (StudyPolicyKey.D_07, StudyPolicyKey.D_19), now)
    finance_body, _ = inspect_study_policy_body(StudyPolicyKey.D_19, finance.body)
    limits = finance_body.specified_value("refund_limits") if finance_body else None
    maximum = limits.get("max_amount_minor") if isinstance(limits, dict) else None
    if (
        command.reason_code not in _manual_refund_reasons(remedy, finance)
        or type(maximum) is not int or command.amount_minor > maximum
        or limits.get("currency") != command.currency
    ):
        raise studytomy_http_error(409, "REFUND_RESERVATION_NOT_APPROVED", "Current policy does not approve this reservation.")
    if command.currency != movement.currency:
        raise studytomy_http_error(409, "REFUND_CURRENCY_MISMATCH", "The refund currency must match the collection.")
    await acquire_write_locks(db, [("collection", movement.id)])
    reservation = StudyRefundReservation
    existing = (await db.execute(select(reservation).where(
        reservation.org_id == locked.id, reservation.refund_reference == str(refund_reference),
    ))).scalar_one_or_none()
    if existing is not None:
        if existing.movement_id != movement.id or any(
            getattr(existing, field) != value for field, value in command.model_dump().items()
        ):
            raise studytomy_http_error(409, "REFUND_REFERENCE_CONFLICT", "The reference already has a different reservation.")
    else:
        request = (await db.execute(_request_event(locked.id, refund_reference).where(
            StudyBusinessEvent.actor_user_id == order.purchaser_user_id,
        ))).scalar_one_or_none()
        if (
            request is None or (request.after_state or {}).get("source_snapshot_digest")
            != _refund_request_digest(order, movement, command)
        ):
            raise studytomy_http_error(409, "REFUND_REQUEST_REQUIRED", "A matching purchaser request is required.")
        reserved = (await db.execute(select(func.coalesce(func.sum(reservation.amount_minor), 0)).where(
            reservation.movement_id == movement.id,
        ))).scalar_one()
        chargebacks = await _chargeback_total(db, locked.id, movement.id)
        if command.amount_minor > movement.amount_minor - reserved - chargebacks:
            raise studytomy_http_error(409, "REFUND_BALANCE_EXCEEDED", "The amount exceeds the unreserved collection balance.")
        authority = finance_body.specified_value("staff_authority")
        evidence = {"status": "reserved"}
        if authority["refund_dual_approval_required"]:
            first = await _retain_or_confirm_first_approval(
                db, locked, actor, order, movement, refund_reference, command, remedy, finance, now,
            )
            if first is None:
                return RefundReservationReceipt(
                    refund_reference=refund_reference, amount_minor=command.amount_minor, currency=command.currency,
                    status="awaiting_second_approval",
                )
            evidence["refund_first_approval_uuid"] = first.event_uuid
        db.add(reservation(
            org_id=locked.id, order_id=order.id, movement_id=movement.id,
            refund_reference=str(refund_reference), requested_by_user_id=order.purchaser_user_id,
            approved_by_user_id=actor.id, remedy_policy_id=remedy.id, finance_policy_id=finance.id,
            **command.model_dump(),
        ))
        await append_business_event(
            db, org_id=locked.id, actor_user_id=actor.id, resource_kind="refund_reservation",
            resource_uuid=str(refund_reference), action="studytomy.refund.reserved",
            reason_code="REFUND_FUNDS_RESERVED", policy_refs=[remedy.id, finance.id],
            after_state=evidence, dedupe_key=f"studytomy:refund:{locked.id}:{refund_reference}",
        )
    return RefundReservationReceipt(
        refund_reference=refund_reference, amount_minor=command.amount_minor, currency=command.currency,
    )


async def attest_refund_settlement(
    db: AsyncSession, org: Organization, actor: PublicUser, *,
    customer_ref: UUID, attempt_reference: UUID, refund_reference: UUID, command: RefundSettlementEvidence,
) -> RefundSettlementReceipt:
    """Attest a verified external return. This service performs no payment operation."""
    org = await lock_organization(db, org.id)
    now = datetime.now(UTC)
    await authorize_studytomy_staff_action(
        db, actor, org, StudytomyCapabilityName.REFUND_APPROVAL,
        target_resource_uuid=str(customer_ref), target_scope_kind="order",
        hide_existence_on_denial=True, now=now,
    )
    found = (await db.execute(
        select(StudyRefundReservation, StudyOrder, StudyMoneyMovement)
        .join(StudyOrder, StudyOrder.id == StudyRefundReservation.order_id)
        .join(StudyMoneyMovement, StudyMoneyMovement.id == StudyRefundReservation.movement_id)
        .join(StudyPaymentAttempt, StudyPaymentAttempt.id == StudyMoneyMovement.attempt_id)
        .where(
            StudyRefundReservation.org_id == org.id, StudyOrder.org_id == org.id,
            StudyOrder.customer_ref == str(customer_ref), StudyPaymentAttempt.method == "manual",
            StudyPaymentAttempt.attempt_reference == str(attempt_reference),
            StudyRefundReservation.refund_reference == str(refund_reference),
        )
    )).one_or_none()
    if found is None:
        raise studytomy_http_error(404, "REFUND_RESERVATION_UNAVAILABLE", "The refund reservation is unavailable.")
    reservation, order, movement = found
    await authorize_financial_delegation(
        db, actor, org, amount_minor=reservation.amount_minor, currency=reservation.currency,
        reason=reservation.reason_code, target_order_uuid=str(customer_ref),
        requester_user_id=reservation.requested_by_user_id, beneficiary_user_id=order.purchaser_user_id, now=now,
    )
    remedy, finance = await _current_policies(db, org, (StudyPolicyKey.D_07, StudyPolicyKey.D_19), now)
    remedy_body, _ = inspect_study_policy_body(StudyPolicyKey.D_07, remedy.body)
    finance_body, _ = inspect_study_policy_body(StudyPolicyKey.D_19, finance.body)
    rules = remedy_body.specified_value("refunds") if remedy_body else None
    authority = finance_body.specified_value("staff_authority") if finance_body else None
    independent = authority.get("manual_refund_settlement_independent_verifier") if isinstance(authority, dict) else None
    access_rule = rules.get("manual_settlement_access_effect") if isinstance(rules, dict) else None
    if (
        access_rule not in ("preserve", "end_fully_returned_order_access")
        or not isinstance(authority, dict) or authority.get("manual_refund_settlement_enabled") is not True
        or type(independent) is not bool
    ):
        raise studytomy_http_error(409, "REFUND_SETTLEMENT_NOT_APPROVED", "Current policy does not approve this settlement.")
    if independent:
        audit = (await db.execute(select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.dedupe_key == f"studytomy:refund:{org.id}:{refund_reference}",
        ))).scalar_one_or_none()
        if audit is None:
            raise studytomy_http_error(409, "REFUND_APPROVAL_EVIDENCE_REQUIRED", "The retained approval evidence is unavailable.")
        first_uuid = (audit.after_state or {}).get("refund_first_approval_uuid")
        first_actor = None
        if first_uuid is not None:
            first_actor = (await db.execute(select(StudyBusinessEvent.actor_user_id).where(
                StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.event_uuid == first_uuid,
                StudyBusinessEvent.action == "studytomy.refund.first_approval",
            ))).scalar_one_or_none()
            if first_actor is None:
                raise studytomy_http_error(409, "REFUND_APPROVAL_EVIDENCE_REQUIRED", "The retained approval evidence is unavailable.")
        if actor.id in (first_actor, reservation.approved_by_user_id):
            raise studytomy_http_error(403, "REFUND_SETTLEMENT_CONFLICT_OF_INTEREST", "Another operator must verify this return.")
    if command.amount_minor != reservation.amount_minor or command.currency != reservation.currency:
        raise studytomy_http_error(409, "REFUND_SETTLEMENT_AMOUNT_MISMATCH", "The return must match the reserved amount and currency.")
    if not reservation.approved_at <= command.settled_at <= now:
        raise studytomy_http_error(409, "REFUND_SETTLEMENT_TIME_INVALID", "The return date must follow reservation approval.")
    await acquire_write_locks(db, [("collection", reservation.movement_id), ("refund_reservation", reservation.id)])
    fields = command.model_dump(exclude={"amount_minor", "currency"})
    settlement_key = f"studytomy:refund:settled:{org.id}:{refund_reference}"
    existing = await db.get(StudyRefundSettlement, reservation.id)
    if existing is not None:
        if any(getattr(existing, name) != value for name, value in fields.items()):
            raise studytomy_http_error(409, "REFUND_SETTLEMENT_CONFLICT", "The reservation already has different settlement evidence.")
        audit = (await db.execute(select(StudyBusinessEvent.after_state).where(
            StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.dedupe_key == settlement_key,
        ))).scalar_one_or_none()
        return RefundSettlementReceipt(
            refund_reference=refund_reference,
            review_required=(audit or {}).get("refund_access_effect") == "review_required",
        )
    duplicate = (await db.execute(select(StudyRefundSettlement.reservation_id).where(
        StudyRefundSettlement.org_id == org.id, StudyRefundSettlement.external_reference == command.external_reference,
    ))).scalar_one_or_none()
    if duplicate is not None:
        raise studytomy_http_error(409, "REFUND_SETTLEMENT_REFERENCE_USED", "The return reference is already retained.")
    db.add(StudyRefundSettlement(
        reservation_id=reservation.id, org_id=org.id, verified_by_user_id=actor.id,
        remedy_policy_id=remedy.id, finance_policy_id=finance.id, verified_at=now, **fields,
    ))
    await db.flush()
    returned = await _settled_refund_total(db, org.id, order, movement.id)
    chargebacks = await _chargeback_total(db, org.id, movement.id)
    overlap = returned + chargebacks > movement.amount_minor
    access_effect = "preserved"
    if overlap:
        access_effect = "review_required"
    elif access_rule == "end_fully_returned_order_access" and returned == order.total_minor:
        await _end_refunded_order_access(db, org.id, order.id, now)
        access_effect = "order_access_ended"
    await append_business_event(
        db, org_id=org.id, actor_user_id=actor.id, subject_uuid=str(customer_ref),
        resource_kind="refund_reservation", resource_uuid=str(refund_reference),
        action="studytomy.refund.settled", reason_code="EXTERNAL_REFUND_SETTLED",
        policy_refs=[remedy.id, finance.id], after_state={
            "status": "settled", "source_snapshot_digest": command.record_sha256, "refund_access_effect": access_effect,
        },
        dedupe_key=settlement_key,
    )
    return RefundSettlementReceipt(refund_reference=refund_reference, review_required=overlap)


async def _chargeback_total(db: AsyncSession, org_id: int, movement_id: int) -> int:
    adjustment = StudyChargebackAdjustment
    return (await db.execute(select(func.coalesce(func.sum(adjustment.amount_minor), 0)).where(
        adjustment.org_id == org_id, adjustment.movement_id == movement_id,
        ~select(StudyChargebackReversal.adjustment_id).where(
            StudyChargebackReversal.adjustment_id == adjustment.id,
        ).exists(),
    ))).scalar_one()


async def _settled_refund_total(db: AsyncSession, org_id: int, order: StudyOrder, movement_id: int) -> int:
    return (await db.execute(
        select(func.coalesce(func.sum(StudyRefundReservation.amount_minor), 0))
        .join(StudyRefundSettlement, StudyRefundSettlement.reservation_id == StudyRefundReservation.id)
        .where(
            StudyRefundReservation.org_id == org_id, StudyRefundReservation.order_id == order.id,
            StudyRefundReservation.movement_id == movement_id, StudyRefundReservation.currency == order.currency,
        )
    )).scalar_one()


async def _end_refunded_order_access(db: AsyncSession, org_id: int, order_id: int, now: datetime) -> None:
    lines = (await db.execute(select(StudyOrderLine.id).where(
        StudyOrderLine.org_id == org_id, StudyOrderLine.order_id == order_id,
    ))).scalars().all()
    grant = StudyCourseAccessGrant
    affected = (await db.execute(select(grant).where(
        grant.org_id == org_id, grant.source_kind == "order_line",
        grant.source_uuid.in_([str(line) for line in lines]), grant.revoked_at.is_(None),
    ).order_by(grant.id).execution_options(populate_existing=True))).scalars().all()
    await acquire_write_locks(db, [("entitlement", row.id) for row in affected])
    for row in affected:
        row.revoked_at = now
