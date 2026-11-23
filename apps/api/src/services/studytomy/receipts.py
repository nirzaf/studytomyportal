"""Purchaser-owned projections of accepted Studytomy orders."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, func, or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.collections import (
    StudyChargebackAdjustment,
    StudyChargebackReversal,
    StudyCollectionRemedy,
    StudyCollectionResolution,
    StudyMoneyMovement,
    StudyPaymentAttempt,
    StudyRefundReservation,
)
from src.db.studytomy.commerce import (
    StudyOfferAccessTerms,
    StudyOfferPriceTerms,
    StudyOfferTerms,
)
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyPolicyKey,
    inspect_study_policy_body,
)
from src.db.studytomy.orders import StudyOrder, StudyOrderAcceptance, StudyOrderLine
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.learners import _current_policies


class ReceiptAllocation(BaseModel):
    position: int
    courses: list[str]
    access: StudyOfferAccessTerms
    price: StudyOfferPriceTerms
    customer_terms: str
    correction_status: Literal["corrected"] | None = None


class OrderSummary(BaseModel):
    customer_ref: UUID
    accepted_at: datetime
    currency: str
    order_total_minor: int


class PurchasePage(BaseModel):
    orders: list[OrderSummary]
    next_cursor: UUID | None


class OrderReceiptView(OrderSummary):
    confirmed_minor: int
    collection_status: Literal["not_required", "awaiting_collection", "pending_collection", "confirmed"]
    finance_review_required: bool
    allocations: list[ReceiptAllocation]


def _my_accepted_orders(org_id: int, purchaser_id: int):
    return (
        select(StudyOrder, StudyOrderAcceptance)
        .join(StudyOrderAcceptance, StudyOrderAcceptance.order_id == StudyOrder.id)
        .where(
            StudyOrder.org_id == org_id, StudyOrder.purchaser_user_id == purchaser_id,
            StudyOrder.sealed_at.is_not(None),
            StudyOrderAcceptance.org_id == org_id, StudyOrderAcceptance.purchaser_user_id == purchaser_id,
        )
    )


def _unavailable() -> HTTPException:
    return HTTPException(404, "Order receipt unavailable.", headers={"Cache-Control": "private, no-store"})


async def require_purchase_receipt_scope(db: AsyncSession, org: Organization) -> list[str]:
    policies = await _current_policies(
        db, org, (StudyPolicyKey.D_05, StudyPolicyKey.D_12, StudyPolicyKey.D_18), datetime.now(UTC),
    )
    information = next(policy for policy in policies if policy.policy_key == StudyPolicyKey.D_12.value)
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_12, information.body)
    uses = body.specified_value("information_uses") if body else None
    receipt_scopes = uses.get("purchase_receipt") if isinstance(uses, dict) else None
    if not isinstance(receipt_scopes, list) or "order_summary" not in receipt_scopes:
        raise studytomy_http_error(409, "RECEIPT_SCOPE_NOT_APPROVED", "Receipt details are unavailable under current policy.")
    return receipt_scopes


async def list_my_purchases(
    db: AsyncSession, org: Organization, purchaser_id: int, *, after: UUID | None, limit: int,
) -> PurchasePage:
    await require_purchase_receipt_scope(db, org)
    query = _my_accepted_orders(org.id, purchaser_id)
    if after is not None:
        pivot = (await db.execute(query.where(StudyOrder.customer_ref == str(after)))).one_or_none()
        if pivot is None:
            raise _unavailable()
        _, acceptance = pivot
        query = query.where(or_(
            StudyOrderAcceptance.accepted_at < acceptance.accepted_at,
            and_(
                StudyOrderAcceptance.accepted_at == acceptance.accepted_at,
                StudyOrder.customer_ref < str(after),
            ),
        ))
    rows = (await db.execute(query.order_by(
        StudyOrderAcceptance.accepted_at.desc(), StudyOrder.customer_ref.desc(),
    ).limit(limit + 1))).all()
    entries = [
        OrderSummary(customer_ref=order.customer_ref, accepted_at=acceptance.accepted_at,
                     currency=order.currency, order_total_minor=order.total_minor)
        for order, acceptance in rows[:limit]
    ]
    return PurchasePage(orders=entries, next_cursor=entries[-1].customer_ref if len(rows) > limit else None)


async def read_my_order_receipt(
    db: AsyncSession, org: Organization, purchaser_id: int, customer_ref: UUID,
) -> OrderReceiptView:
    found = (await db.execute(_my_accepted_orders(org.id, purchaser_id).where(
        StudyOrder.customer_ref == str(customer_ref),
    ))).one_or_none()
    if found is None:
        raise _unavailable()
    order, acceptance = found
    scopes = await require_purchase_receipt_scope(db, org)
    lines = (await db.execute(select(StudyOrderLine).where(
        StudyOrderLine.org_id == org.id, StudyOrderLine.order_id == order.id,
    ).order_by(StudyOrderLine.id))).scalars().all()
    corrected_lines: set[str] = set()
    if lines and "allocation_correction_status" in scopes:
        corrected_lines = set((await db.execute(select(StudyBusinessEvent.resource_uuid).where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.resource_kind == "order_line",
            StudyBusinessEvent.resource_uuid.in_([str(line.id) for line in lines]),
            StudyBusinessEvent.action == "studytomy.allocation.corrected",
        ))).scalars().all())
    allocations = []
    for position, line in enumerate(lines, start=1):
        terms = StudyOfferTerms.model_validate(line.terms_snapshot)
        if terms.access is None or terms.price is None or terms.customer_terms is None:
            raise studytomy_http_error(409, "RECEIPT_TERMS_UNAVAILABLE", "The retained receipt terms are unavailable.")
        allocations.append(ReceiptAllocation(
            position=position, courses=[resource.title_snapshot for resource in terms.resources or []
                                        if resource.title_snapshot],
            access=terms.access, price=terms.price, customer_terms=terms.customer_terms,
            correction_status="corrected" if str(line.id) in corrected_lines else None,
        ))
    movements = (await db.execute(select(StudyMoneyMovement.amount_minor, StudyMoneyMovement.currency).where(
        StudyMoneyMovement.org_id == org.id, StudyMoneyMovement.order_id == order.id,
    ))).all()
    confirmed_minor = sum(amount for amount, currency in movements if currency == order.currency)
    review = (await db.execute(select(StudyCollectionRemedy.attempt_id)
        .outerjoin(StudyCollectionResolution, StudyCollectionResolution.attempt_id == StudyCollectionRemedy.attempt_id)
        .where(
            StudyCollectionRemedy.org_id == org.id, StudyCollectionRemedy.order_id == order.id,
            StudyCollectionResolution.attempt_id.is_(None),
        ).limit(1))).scalar_one_or_none()
    movement = StudyMoneyMovement
    reservation = StudyRefundReservation
    debit = StudyChargebackAdjustment
    reserved = select(func.coalesce(func.sum(reservation.amount_minor), 0)).where(
        reservation.org_id == movement.org_id, reservation.movement_id == movement.id,
    ).correlate(movement).scalar_subquery()
    charged_back = select(func.coalesce(func.sum(debit.amount_minor), 0)).where(
        debit.org_id == movement.org_id, debit.movement_id == movement.id,
        ~select(StudyChargebackReversal.adjustment_id).where(
            StudyChargebackReversal.adjustment_id == debit.id,
        ).exists(),
    ).correlate(movement).scalar_subquery()
    overlap = (await db.execute(select(movement.id).where(
        movement.org_id == org.id, movement.order_id == order.id,
        charged_back > 0, reserved + charged_back > movement.amount_minor,
    ).limit(1))).scalar_one_or_none()
    attempted = (await db.execute(select(StudyPaymentAttempt.id).where(
        StudyPaymentAttempt.org_id == org.id, StudyPaymentAttempt.order_id == order.id,
    ).limit(1))).scalar_one_or_none()
    status: Literal["not_required", "awaiting_collection", "pending_collection", "confirmed"]
    if order.total_minor == 0:
        status = "not_required"
    elif confirmed_minor == order.total_minor:
        status = "confirmed"
    else:
        status = "pending_collection" if attempted is not None else "awaiting_collection"
    return OrderReceiptView(
        customer_ref=customer_ref, accepted_at=acceptance.accepted_at, currency=order.currency,
        order_total_minor=order.total_minor, confirmed_minor=confirmed_minor, collection_status=status,
        finance_review_required=review is not None or overlap is not None
        or (bool(movements) and confirmed_minor != order.total_minor)
        or any(currency != order.currency for _, currency in movements),
        allocations=allocations,
    )
