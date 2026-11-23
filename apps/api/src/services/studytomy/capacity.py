"""Transaction-scoped reservation primitives for approved finite capacity."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.capacity import StudyCapacityHold
from src.db.studytomy.commerce import StudyOffer, StudyOfferTerms
from src.db.studytomy.governance import StudyPolicyKey, inspect_study_policy_body
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.learners import _current_policies


async def resolve_finite_capacity_terms(
    db: AsyncSession, org: Organization, offer_uuid: str, terms: StudyOfferTerms, now: datetime,
) -> tuple[int, timedelta]:
    """Resolve finite capacity from named decisions for a proposed offer."""
    policies = await _current_policies(
        db, org, (StudyPolicyKey.D_04, StudyPolicyKey.D_05, StudyPolicyKey.D_17), now,
    )
    values = {}
    for policy, area, field in zip(
        policies,
        ("staffing", "learner_allocation", "pilot_size"),
        ("offer_place_limits", "offer_hold_minutes", "offer_place_limits"),
        strict=True,
    ):
        body, _ = inspect_study_policy_body(policy.policy_key, policy.body)
        decisions = body.specified_value(area) if body else None
        per_offer = decisions.get(field) if isinstance(decisions, dict) else None
        values[policy.policy_key] = per_offer.get(offer_uuid) if isinstance(per_offer, dict) else None

    staffing = values["D-04"]
    hold_minutes = values["D-05"]
    pilot = values["D-17"]
    if (
        terms.capacity_is_limited is not True
        or terms.capacity_limit is None
        or any(type(value) is not int or not 1 <= value <= 10_000_000 for value in (staffing, hold_minutes, pilot))
        or terms.capacity_limit > min(staffing, pilot)
    ):
        raise studytomy_http_error(
            409, "OFFER_CAPACITY_POLICY_UNCONFIGURED",
            "Finite capacity and hold expiry require matching approved offer decisions.",
        )
    return terms.capacity_limit, timedelta(minutes=hold_minutes)


async def _lock_offer(db: AsyncSession, org_id: int, offer_id: int) -> None:
    locked = (await db.execute(select(StudyOffer.id).where(
        StudyOffer.org_id == org_id, StudyOffer.id == offer_id,
    ).with_for_update())).scalar_one_or_none()
    if locked is None:
        raise studytomy_http_error(404, "OFFER_NOT_FOUND", "Offer not found.")


async def _hold(db: AsyncSession, offer_id: int, request_uuid: str) -> StudyCapacityHold | None:
    return (await db.execute(select(StudyCapacityHold).where(
        StudyCapacityHold.offer_id == offer_id,
        StudyCapacityHold.request_uuid == request_uuid,
    ).execution_options(populate_existing=True))).scalar_one_or_none()


async def reserve_offer_capacity(
    db: AsyncSession, *, org_id: int, offer_id: int, request_uuid: str, places: int,
    approved_limit: int, approved_hold_duration: timedelta, sales_open: bool,
) -> StudyCapacityHold:
    """Call only after policy, sale state and allocation checks in the same transaction."""
    if (
        not 1 <= places <= approved_limit
        or approved_hold_duration <= timedelta()
        or not 1 <= len(request_uuid) <= 100
    ):
        raise ValueError("Invalid approved capacity reservation terms")
    await _lock_offer(db, org_id, offer_id)
    existing = await _hold(db, offer_id, request_uuid)
    if existing:
        if existing.places != places:
            raise studytomy_http_error(409, "CAPACITY_REQUEST_CONFLICT", "The reservation terms changed.")
        return existing
    if not sales_open:
        raise studytomy_http_error(409, "OFFER_SALES_PAUSED", "New reservations are paused.")
    now = datetime.now(UTC)
    hold = StudyCapacityHold
    occupied = (await db.execute(select(func.coalesce(func.sum(hold.places), 0)).where(
        hold.offer_id == offer_id,
        or_(hold.consumed_at.is_not(None), hold.expires_at > now),
    ))).scalar_one()
    if occupied + places > approved_limit:
        raise studytomy_http_error(409, "OFFER_CAPACITY_UNAVAILABLE", "There are not enough places.")
    reservation = hold(
        offer_id=offer_id, request_uuid=request_uuid, places=places,
        created_at=now, expires_at=now + approved_hold_duration,
    )
    db.add(reservation)
    await db.flush()
    return reservation


async def consume_offer_capacity(
    db: AsyncSession, *, org_id: int, offer_id: int, request_uuid: str, sales_open: bool,
) -> StudyCapacityHold:
    """Consume after verified confirmation, or return a conflict for remedy review."""
    await _lock_offer(db, org_id, offer_id)
    reservation = await _hold(db, offer_id, request_uuid)
    if reservation is not None and reservation.consumed_at is not None:
        return reservation
    now = datetime.now(UTC)
    if (
        not sales_open or reservation is None
        or reservation.expires_at.replace(tzinfo=reservation.expires_at.tzinfo or UTC) <= now
    ):
        raise studytomy_http_error(
            409, "CAPACITY_REMEDY_REVIEW_REQUIRED",
            "Collection requires review because its reservation cannot be consumed.",
        )
    reservation.consumed_at = now
    await db.flush()
    return reservation
