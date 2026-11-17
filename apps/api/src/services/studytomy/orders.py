"""Server-side quote calculation for the selected immutable offer version."""

from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import BaseModel, ValidationError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.access import StudyCourseAccessGrant
from src.db.studytomy.commerce import (
    StudyOffer,
    StudyOfferPriceLineKind,
    StudyOfferPriceTerms,
    StudyOfferTerms,
    StudyOfferVersion,
)
from src.db.studytomy.family import (
    StudyEligibilityKind,
    StudyLearnerControlMode,
    StudyLearnerProfile,
    StudyLearnerProfileStatus,
)
from src.db.studytomy.governance import (
    StudyPolicyBody,
    StudyPolicyKey,
    inspect_study_policy_body,
)
from src.db.studytomy.orders import StudyOrder, StudyOrderAcceptance, StudyOrderLine
from src.db.user_organizations import UserOrganization
from src.services.studytomy.authority import has_active_guardian_scope
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.capacity import (
    reserve_offer_capacity,
    resolve_finite_capacity_terms,
)
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.idempotency import (
    canonical_request_hash,
    claim_idempotency,
    complete_idempotency,
)
from src.services.studytomy.learners import (
    _active_policy,
    _current_policies,
    _decision_read,
    _latest_decisions,
)
from src.services.studytomy.offers import _snapshot_resource_titles, _terms_snapshot
from src.services.studytomy.policies import lock_organization


class OrderQuote(BaseModel):
    offer_version_uuid: str
    offer_sha256: str
    price_per_learner: StudyOfferPriceTerms
    learner_count: int
    total_minor: int


class NamedOrderQuote(OrderQuote):
    learner_uuids: list[str]
    learners_with_overlap: list[str]
    customer_terms: str
    confirmation_sha256: str


def _require_pilot_offer(pilot, offer_uuid: str, offer_version_uuid: str) -> None:
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_17, pilot.body)
    pilot_size = body.specified_value("pilot_size") if body else None
    versions = pilot_size.get("order_acceptance_offer_versions") if isinstance(pilot_size, dict) else None
    if not isinstance(versions, dict) or versions.get(offer_uuid) != offer_version_uuid:
        raise studytomy_http_error(409, "OFFER_SALES_PAUSED", "The exact offer version is not open to the pilot.")


async def require_pilot_checkout(
    db: AsyncSession, org: Organization, *, offer_uuid: str, offer_version_uuid: str,
) -> None:
    pilot = await _active_policy(db, org, StudyPolicyKey.D_17, datetime.now(UTC))
    _require_pilot_offer(pilot, offer_uuid, offer_version_uuid)


async def preview_named_order(
    db: AsyncSession, org: Organization, *, purchaser_id: int,
    offer_uuid: str, offer_version_uuid: str, learner_uuids: list[str],
) -> NamedOrderQuote:
    """Prepare a private checkout quote after checking every named allocation."""
    if not 1 <= len(learner_uuids) <= 100 or len(set(learner_uuids)) != len(learner_uuids):
        raise studytomy_http_error(422, "ORDER_LEARNERS_INVALID", "Choose distinct named learners.")
    matches = (await db.execute(select(StudyLearnerProfile.learner_uuid, StudyLearnerProfile.id).where(
        StudyLearnerProfile.org_id == org.id,
        StudyLearnerProfile.learner_uuid.in_(learner_uuids),
    ))).all()
    profiles = dict(matches)
    now = datetime.now(UTC)
    for learner_uuid in learner_uuids:
        profile_id = profiles.get(learner_uuid)
        if profile_id is None or not await can_purchase_for_learner(
            db, org, purchaser_id=purchaser_id, learner_profile_id=profile_id, now=now,
        ):
            raise studytomy_http_error(
                404, "ORDER_LEARNER_UNAVAILABLE", "One or more selected learners are unavailable.",
            )

    quote = await calculate_order_quote(
        db, org, offer_uuid=offer_uuid, offer_version_uuid=offer_version_uuid, learner_count=len(profiles),
    )
    snapshot = (await db.execute(select(StudyOfferVersion.terms).where(
        StudyOfferVersion.version_uuid == quote.offer_version_uuid,
    ))).scalar_one()
    terms = StudyOfferTerms.model_validate(snapshot)
    policy = await _active_policy(db, org, StudyPolicyKey.D_18, now)
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_18, policy.body)
    approved_terms = body.specified_value("customer_terms") if body else None
    approved = approved_terms.get(offer_uuid) if isinstance(approved_terms, dict) else None
    if not terms.customer_terms or approved != terms.customer_terms:
        raise studytomy_http_error(
            409, "OFFER_CUSTOMER_TERMS_NOT_APPROVED", "The offer lacks current approved customer terms.",
        )
    grant = StudyCourseAccessGrant
    overlapping = set((await db.execute(select(grant.learner_profile_id).where(
        grant.org_id == org.id,
        grant.learner_profile_id.in_(list(profiles.values())),
        grant.course_uuid.in_([resource.resource_uuid for resource in terms.resources or []]),
        grant.revoked_at.is_(None),
        grant.created_at <= now,
        (grant.ends_at.is_(None) | (grant.ends_at > now)),
    ))).scalars().all())
    projection = {
        **quote.model_dump(mode="json"),
        "learner_uuids": learner_uuids,
        "learners_with_overlap": [uuid for uuid in learner_uuids if profiles[uuid] in overlapping],
        "customer_terms": terms.customer_terms,
    }
    return NamedOrderQuote(
        **projection,
        confirmation_sha256=canonical_request_hash({
            "org_id": org.id, "purchaser_id": purchaser_id, "offer_uuid": offer_uuid,
            "quote": projection,
        }),
    )


async def verify_named_order_confirmation(
    db: AsyncSession, org: Organization, *, purchaser_id: int, offer_uuid: str,
    offer_version_uuid: str, learner_uuids: list[str], confirmation_sha256: str,
    acknowledged_overlap: bool,
) -> NamedOrderQuote:
    """Recheck the exact decision under the organization lock before accepting it."""
    locked = await lock_organization(db, org.id)
    quote = await preview_named_order(
        db, locked, purchaser_id=purchaser_id, offer_uuid=offer_uuid,
        offer_version_uuid=offer_version_uuid, learner_uuids=learner_uuids,
    )
    if quote.confirmation_sha256 != confirmation_sha256:
        raise studytomy_http_error(
            409, "ORDER_QUOTE_CHANGED", "Review the current quote before confirming.",
        )
    if quote.learners_with_overlap and not acknowledged_overlap:
        raise studytomy_http_error(
            409, "ORDER_OVERLAP_ACKNOWLEDGEMENT_REQUIRED", "Review and acknowledge overlapping access.",
        )
    return quote


async def record_named_order_draft(
    db: AsyncSession, org: Organization, *, purchaser_id: int, offer_uuid: str,
    offer_version_uuid: str, learner_uuids: list[str], confirmation_sha256: str,
    acknowledged_overlap: bool, idempotency_key: str,
) -> str:
    """Persist a reviewed quote as a private draft in the caller's transaction."""
    locked = await lock_organization(db, org.id)
    operation = "studytomy.order.draft.record"
    scope = {
        "org_id": locked.id, "actor_user_id": purchaser_id,
        "operation": operation, "idempotency_key": idempotency_key,
    }
    submitted = {
        "purchaser_id": purchaser_id, "offer_uuid": offer_uuid,
        "offer_version_uuid": offer_version_uuid, "learner_uuids": learner_uuids,
        "confirmation_sha256": confirmation_sha256, "acknowledged_overlap": acknowledged_overlap,
    }
    replay = await claim_idempotency(db, **scope, request_payload=submitted)
    if replay is not None:
        reference = (await db.execute(select(StudyOrder.customer_ref).where(
            StudyOrder.org_id == locked.id, StudyOrder.purchaser_user_id == purchaser_id,
            StudyOrder.customer_ref == replay.resource_uuid,
            StudyOrder.sealed_at.is_not(None),
        ))).scalar_one_or_none()
        if reference is None:
            raise studytomy_http_error(409, "ORDER_DRAFT_REPLAY_UNAVAILABLE", "The saved draft is unavailable.")
        return reference

    quote = await verify_named_order_confirmation(db, locked, **submitted)
    row = (await db.execute(
        select(StudyOfferVersion).join(StudyOffer, StudyOffer.id == StudyOfferVersion.offer_id)
        .where(StudyOffer.org_id == locked.id, StudyOffer.offer_uuid == offer_uuid,
               StudyOfferVersion.version_uuid == quote.offer_version_uuid)
    )).scalar_one()
    profile_ids = dict((await db.execute(
        select(StudyLearnerProfile.learner_uuid, StudyLearnerProfile.id)
        .where(StudyLearnerProfile.org_id == locked.id, StudyLearnerProfile.learner_uuid.in_(learner_uuids))
    )).all())
    order = StudyOrder(
        org_id=locked.id, purchaser_user_id=purchaser_id,
        currency=quote.price_per_learner.currency, total_minor=quote.total_minor,
        confirmation_sha256=quote.confirmation_sha256,
    )
    db.add(order)
    await db.flush()
    db.add_all([
        StudyOrderLine(
            org_id=locked.id, order_id=order.id, learner_profile_id=profile_ids[learner_uuid],
            offer_id=row.offer_id, offer_version_no=row.version_no,
            total_minor=quote.price_per_learner.total_minor, terms_snapshot=row.terms,
        )
        for learner_uuid in learner_uuids
    ])
    await db.flush()
    order.sealed_at = datetime.now(UTC)
    await db.flush()
    await append_business_event(
        db, org_id=locked.id, actor_user_id=purchaser_id, resource_kind="order",
        resource_uuid=order.customer_ref, action="studytomy.order.draft_recorded",
        reason_code="ORDER_DRAFT_RECORDED",
        after_state={"status": "draft", "source_snapshot_digest": confirmation_sha256},
        dedupe_key=f"studytomy:order:draft:{order.customer_ref}",
    )
    await complete_idempotency(
        db, **scope, resource_kind="order", resource_uuid=order.customer_ref,
    )
    return order.customer_ref


async def accept_named_order(
    db: AsyncSession, org: Organization, *, purchaser_id: int, customer_ref: str,
    offer_uuid: str, offer_version_uuid: str, learner_uuids: list[str],
    confirmation_sha256: str, acknowledged_overlap: bool,
) -> StudyOrderAcceptance:
    """Record purchaser acceptance and a capacity hold in the caller's transaction."""
    locked = await lock_organization(db, org.id)
    order = (await db.execute(select(StudyOrder).where(
        StudyOrder.org_id == locked.id, StudyOrder.purchaser_user_id == purchaser_id,
        StudyOrder.customer_ref == customer_ref, StudyOrder.sealed_at.is_not(None),
    ))).scalar_one_or_none()
    if order is None:
        raise studytomy_http_error(404, "ORDER_DRAFT_UNAVAILABLE", "The order draft is unavailable.")
    if order.confirmation_sha256 != confirmation_sha256:
        raise studytomy_http_error(409, "ORDER_QUOTE_CHANGED", "Review the current quote before confirming.")
    existing = await db.get(StudyOrderAcceptance, order.id)
    if existing is not None:
        if existing.acknowledged_overlap != acknowledged_overlap:
            raise studytomy_http_error(409, "ORDER_ACCEPTANCE_CONFLICT", "The order already retains a different acknowledgement.")
        return existing

    quote = await verify_named_order_confirmation(
        db, locked, purchaser_id=purchaser_id, offer_uuid=offer_uuid,
        offer_version_uuid=offer_version_uuid, learner_uuids=learner_uuids,
        confirmation_sha256=confirmation_sha256, acknowledged_overlap=acknowledged_overlap,
    )
    if order.currency != quote.price_per_learner.currency or order.total_minor != quote.total_minor:
        raise studytomy_http_error(409, "ORDER_QUOTE_CHANGED", "Review the current quote before confirming.")
    now = datetime.now(UTC)
    policies = await _current_policies(db, locked, (
        StudyPolicyKey.D_01, StudyPolicyKey.D_03, StudyPolicyKey.D_05,
        StudyPolicyKey.D_17, StudyPolicyKey.D_18, StudyPolicyKey.D_20,
    ), now)
    pilot = next(policy for policy in policies if policy.policy_key == StudyPolicyKey.D_17.value)
    _require_pilot_offer(pilot, offer_uuid, offer_version_uuid)

    offer, version = (await db.execute(
        select(StudyOffer, StudyOfferVersion)
        .join(StudyOfferVersion, StudyOfferVersion.offer_id == StudyOffer.id)
        .where(StudyOffer.org_id == locked.id, StudyOffer.offer_uuid == offer_uuid,
               StudyOfferVersion.version_uuid == quote.offer_version_uuid)
    )).one()
    if offer.offer_kind != "course":
        raise studytomy_http_error(409, "OFFER_KIND_NOT_OPEN", "Only course offers are enabled for the pilot.")
    lines = (await db.execute(
        select(StudyOrderLine, StudyLearnerProfile.learner_uuid)
        .join(StudyLearnerProfile, StudyLearnerProfile.id == StudyOrderLine.learner_profile_id)
        .where(StudyOrderLine.org_id == locked.id, StudyOrderLine.order_id == order.id)
    )).all()
    if sorted(uuid for _, uuid in lines) != sorted(learner_uuids) or any(
        line.offer_id != offer.id or line.offer_version_no != version.version_no
        or line.terms_snapshot != version.terms or line.total_minor != quote.price_per_learner.total_minor
        for line, _ in lines
    ):
        raise studytomy_http_error(409, "ORDER_QUOTE_CHANGED", "The saved allocations differ from the confirmed quote.")
    terms = StudyOfferTerms.model_validate(version.terms)
    if terms.completion_blockers():
        raise studytomy_http_error(409, "OFFER_TERMS_INCOMPLETE", "The offer terms are incomplete.")
    current = await _snapshot_resource_titles(
        db, locked, terms, require_current_release_policies_at=now,
    )
    if _terms_snapshot(current, offer.offer_kind)[1] != quote.offer_sha256:
        raise studytomy_http_error(409, "ORDER_QUOTE_CHANGED", "The referenced offer content changed.")
    if terms.capacity_is_limited:
        limit, duration = await resolve_finite_capacity_terms(db, locked, offer_uuid, terms, now)
        await reserve_offer_capacity(
            db, org_id=locked.id, offer_id=version.offer_id, request_uuid=customer_ref,
            places=quote.learner_count, approved_limit=limit, approved_hold_duration=duration, sales_open=True,
        )
    acceptance = StudyOrderAcceptance(
        org_id=locked.id, order_id=order.id, purchaser_user_id=purchaser_id,
        confirmation_sha256=confirmation_sha256, acknowledged_overlap=acknowledged_overlap,
        policy_binding={policy.policy_key: {
            "uuid": policy.policy_uuid, "version": policy.version, "body_sha256": canonical_request_hash(policy.body),
        } for policy in policies},
    )
    db.add(acceptance)
    await append_business_event(
        db, org_id=locked.id, actor_user_id=purchaser_id, resource_kind="order",
        resource_uuid=order.customer_ref, action="studytomy.order.accepted",
        reason_code="ORDER_ACCEPTED", policy_refs=[int(policy.id) for policy in policies],
        after_state={"status": "accepted", "source_snapshot_digest": confirmation_sha256},
        dedupe_key=f"studytomy:order:acceptance:{order.customer_ref}",
    )
    await db.flush()
    return acceptance


async def can_purchase_for_learner(
    db: AsyncSession, org: Organization, *, purchaser_id: int, learner_profile_id: int, now: datetime,
) -> bool:
    """Evaluate current purchase authority without exposing learner evidence."""
    profile = (await db.execute(select(StudyLearnerProfile).where(
        StudyLearnerProfile.id == learner_profile_id,
        StudyLearnerProfile.org_id == org.id,
        StudyLearnerProfile.status == StudyLearnerProfileStatus.ACTIVE.value,
    ).execution_options(populate_existing=True))).scalar_one_or_none()
    if profile is None:
        return False
    members = set((await db.execute(select(UserOrganization.user_id).where(
        UserOrganization.org_id == org.id,
        UserOrganization.user_id.in_({purchaser_id, profile.user_id}),
    ))).scalars().all())
    if not {purchaser_id, profile.user_id}.issubset(members):
        return False

    self_purchase = purchaser_id == profile.user_id
    if self_purchase:
        if profile.control_mode != StudyLearnerControlMode.INDEPENDENT.value:
            return False
    else:
        purchaser = (await db.execute(select(StudyLearnerProfile).where(
            StudyLearnerProfile.org_id == org.id,
            StudyLearnerProfile.user_id == purchaser_id,
        ).execution_options(populate_existing=True))).scalar_one_or_none()
        if purchaser is not None and (
            purchaser.control_mode != StudyLearnerControlMode.INDEPENDENT.value
            or purchaser.status != StudyLearnerProfileStatus.ACTIVE.value
        ):
            return False
        if not await has_active_guardian_scope(db, org, profile, purchaser_id, "purchase_allocation", now=now):
            return False

    required = {StudyEligibilityKind.LEARNING.value}
    if self_purchase:
        required.add(StudyEligibilityKind.PURCHASE.value)
    try:
        decisions = await _latest_decisions(db, profile)
    except HTTPException as error:
        if error.status_code != 409:
            raise
        return False
    for decision in decisions:
        if decision.eligibility_kind in required:
            if not (await _decision_read(db, org, decision, now)).currently_eligible:
                return False
            required.remove(decision.eligibility_kind)
    return not required


async def calculate_order_quote(
    db: AsyncSession, org: Organization, *, offer_uuid: str, offer_version_uuid: str, learner_count: int,
) -> OrderQuote:
    """Verify current prices for a candidate order without recording acceptance."""
    if not 1 <= learner_count <= 100:
        raise studytomy_http_error(422, "ORDER_LEARNER_COUNT_INVALID", "The learner count is invalid.")
    row = (await db.execute(
        select(StudyOffer, StudyOfferVersion)
        .join(StudyOfferVersion, StudyOfferVersion.offer_id == StudyOffer.id)
        .where(
            StudyOffer.org_id == org.id, StudyOffer.offer_uuid == offer_uuid,
            StudyOfferVersion.version_no == StudyOffer.current_version_no,
            StudyOfferVersion.version_uuid == offer_version_uuid,
        )
    )).one_or_none()
    if row is None:
        raise studytomy_http_error(409, "OFFER_VERSION_NOT_CURRENT", "Reload the offer before proceeding.")
    offer, version = row
    try:
        terms = StudyOfferTerms.model_validate(version.terms)
    except ValidationError as error:
        raise studytomy_http_error(409, "OFFER_PRICE_UNAVAILABLE", "The saved offer terms are invalid.") from error
    _, digest = _terms_snapshot(terms, offer.offer_kind)
    price = terms.price
    if digest != version.content_sha256 or price is None or not price.is_complete():
        raise studytomy_http_error(409, "OFFER_PRICE_UNAVAILABLE", "The saved offer price is unavailable.")
    now = datetime.now(UTC)
    pricing = await _active_policy(db, org, StudyPolicyKey.D_05, now)
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_05, pricing.body)
    approved_prices = body.specified_value("prices") if body else None
    approved_price = approved_prices.get(offer_uuid) if isinstance(approved_prices, dict) else None
    if canonical_request_hash(approved_price) != canonical_request_hash(price.model_dump(mode="json")):
        raise studytomy_http_error(409, "OFFER_PRICE_NOT_APPROVED", "The current price lacks exact approval.")
    if price.total_minor == 0:
        require_free_order_version(body, offer_uuid, offer_version_uuid)
    discounts = [line.model_dump(mode="json") for line in price.lines if line.kind == StudyOfferPriceLineKind.DISCOUNT]
    if discounts:
        promotion = await _active_policy(db, org, StudyPolicyKey.D_20, now)
        body, _ = inspect_study_policy_body(StudyPolicyKey.D_20, promotion.body)
        approved_discounts = body.specified_value("discounts") if body else None
        approved = approved_discounts.get(offer_uuid) if isinstance(approved_discounts, dict) else None
        if canonical_request_hash(approved) != canonical_request_hash(discounts):
            raise studytomy_http_error(409, "OFFER_DISCOUNT_NOT_APPROVED", "The current discount lacks exact approval.")
    return OrderQuote(
        offer_version_uuid=version.version_uuid, offer_sha256=digest, price_per_learner=price,
        learner_count=learner_count, total_minor=price.total_minor * learner_count,
    )


def require_free_order_version(
    body: StudyPolicyBody | None, offer_uuid: str, offer_version_uuid: str,
) -> None:
    allocation = body.specified_value("learner_allocation") if body else None
    versions = allocation.get("free_order_offer_versions") if isinstance(allocation, dict) else None
    if not isinstance(versions, dict) or versions.get(offer_uuid) != offer_version_uuid:
        raise studytomy_http_error(
            409, "FREE_ORDER_NOT_APPROVED", "Free allocation requires explicit approval for this offer version.",
        )
