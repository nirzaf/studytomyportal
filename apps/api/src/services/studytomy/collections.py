"""Staff-led registration of collection attempts for accepted orders."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.access import StudyCourseAccessGrant
from src.db.studytomy.capacity import StudyCapacityHold
from src.db.studytomy.collections import (
    StudyCollectionRemedy,
    StudyCollectionResolution,
    StudyManualCollectionEvidence,
    StudyMoneyMovement,
    StudyPaymentAttempt,
)
from src.db.studytomy.commerce import (
    StudyOffer,
    StudyOfferAccessClock,
    StudyOfferTerms,
    StudyOfferVersion,
)
from src.db.studytomy.education import StudyCourseRelease
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyOwnerAssignment,
    StudyOwnerFunction,
    StudyPolicyKey,
    StudyPolicyVersion,
    StudyStaffScope,
    inspect_study_policy_body,
)
from src.db.studytomy.orders import StudyOrder, StudyOrderAcceptance, StudyOrderLine
from src.db.users import PublicUser
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_studytomy_staff_action,
)
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.capacity import consume_offer_capacity
from src.services.studytomy.entitlements import (
    current_allocation_delivery_policy,
    persist_authorized_course_grant,
)
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.learners import _active_policy, _current_policies
from src.services.studytomy.offers import _snapshot_resource_titles
from src.services.studytomy.orders import (
    can_purchase_for_learner,
    require_free_order_version,
    require_pilot_checkout,
)
from src.services.studytomy.policies import lock_organization


class ManualBankEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    external_reference: str = Field(min_length=1, max_length=150)
    evidence_reference: str = Field(min_length=1, max_length=150)
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    amount_minor: int = Field(strict=True, gt=0, le=9_000_000_000_000_000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    settled_at: AwareDatetime


class ManualRemedyResolution(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    outcome: Literal["returned_externally", "settlement_not_received"]
    evidence_reference: str = Field(min_length=1, max_length=150)
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class FinanceReviewEntry(BaseModel):
    customer_ref: UUID
    attempt_reference: UUID
    reason: str
    opened_at: datetime


class FinanceReviewPage(BaseModel):
    cases: list[FinanceReviewEntry]
    next_cursor: UUID | None


async def _manual_finance_policy(
    db: AsyncSession, org: Organization, now: datetime,
) -> StudyPolicyVersion:
    policy = await _active_policy(db, org, StudyPolicyKey.D_19, now)
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_19, policy.body)
    authority = body.specified_value("staff_authority") if body else None
    if not isinstance(authority, dict) or authority.get("manual_collection_enabled") is not True:
        raise studytomy_http_error(409, "MANUAL_COLLECTION_NOT_APPROVED", "Staff-led collection is unavailable.")
    return policy


async def list_manual_collection_reviews(
    db: AsyncSession, org: Organization, actor: PublicUser, *, after: UUID | None, limit: int,
) -> FinanceReviewPage:
    """List only unresolved cases that pass the current exact finance ACL."""
    now = datetime.now(UTC)
    capability = StudytomyCapabilityName.FINANCIAL_RECONCILIATION
    scope = StudyStaffScope
    owner = StudyOwnerAssignment
    exact_assignment = (
        select(scope.id).join(owner, owner.id == scope.assignment_id)
        .where(
            scope.org_id == org.id, scope.scope_kind == "collection", scope.scope_uuid == StudyOrder.customer_ref,
            scope.status == "active", scope.is_exclusion.is_(False),
            scope.capability.in_((capability.value, f"studytomy.{capability.value}")),
            owner.org_id == org.id, owner.user_id == actor.id, owner.status == "active",
            owner.scope_kind == "organization", owner.scope_uuid == org.org_uuid,
            owner.responsibility == StudyOwnerFunction.COMMERCIAL.value,
            owner.effective_from <= now, or_(owner.effective_to.is_(None), owner.effective_to > now),
        ).exists()
    )
    query = (
        select(StudyOrder.customer_ref, StudyPaymentAttempt.attempt_reference,
               StudyCollectionRemedy.reason, StudyCollectionRemedy.opened_at)
        .select_from(StudyCollectionRemedy)
        .join(StudyOrder, StudyOrder.id == StudyCollectionRemedy.order_id)
        .join(StudyPaymentAttempt, StudyPaymentAttempt.id == StudyCollectionRemedy.attempt_id)
        .outerjoin(StudyCollectionResolution, StudyCollectionResolution.attempt_id == StudyCollectionRemedy.attempt_id)
        .where(
            StudyCollectionRemedy.org_id == org.id, StudyOrder.org_id == org.id,
            StudyOrder.purchaser_user_id != actor.id, StudyPaymentAttempt.method == "manual",
            StudyCollectionResolution.attempt_id.is_(None), exact_assignment,
        )
        .order_by(StudyPaymentAttempt.attempt_reference)
        .limit(limit + 1)
    )
    cases = []
    cursor = str(after) if after else ""
    checked_policy = False
    while len(cases) <= limit:
        rows = (await db.execute(query.where(StudyPaymentAttempt.attempt_reference > cursor))).all()
        if rows and not checked_policy:
            await _manual_finance_policy(db, org, now)
            checked_policy = True
        for ref, attempt_ref, reason, opened_at in rows:
            try:
                await authorize_studytomy_staff_action(
                    db, actor, org, capability, target_scope_kind="collection",
                    target_resource_uuid=ref, hide_existence_on_denial=True, now=now,
                )
            except HTTPException as error:
                if error.status_code not in (403, 404):
                    raise
                continue
            cases.append(FinanceReviewEntry(
                customer_ref=ref, attempt_reference=attempt_ref, reason=reason, opened_at=opened_at,
            ))
            if len(cases) > limit:
                break
        if len(rows) <= limit:
            break
        cursor = rows[-1][1]
    return FinanceReviewPage(cases=cases[:limit], next_cursor=cases[limit - 1].attempt_reference if len(cases) > limit else None)


async def _manual_collection_context(
    db: AsyncSession, org: Organization, actor: PublicUser, customer_ref: UUID,
) -> tuple[StudyOrder, StudyPolicyVersion]:
    locked = await lock_organization(db, org.id)
    ref = str(customer_ref)
    await authorize_studytomy_staff_action(
        db, actor, locked, StudytomyCapabilityName.FINANCIAL_RECONCILIATION,
        target_scope_kind="collection", target_resource_uuid=ref, hide_existence_on_denial=True,
    )
    now = datetime.now(UTC)
    policy = await _manual_finance_policy(db, locked, now)
    order = (await db.execute(
        select(StudyOrder).join(StudyOrderAcceptance, StudyOrderAcceptance.order_id == StudyOrder.id)
        .where(StudyOrder.org_id == locked.id, StudyOrder.customer_ref == ref)
    )).scalar_one_or_none()
    if order is None:
        raise studytomy_http_error(404, "COLLECTION_ORDER_UNAVAILABLE", "The collection order is unavailable.")
    if actor.id == order.purchaser_user_id:
        raise studytomy_http_error(403, "COLLECTION_CONFLICT_OF_INTEREST", "Another operator must handle this collection.")
    if order.total_minor <= 0:
        raise studytomy_http_error(409, "COLLECTION_NOT_REQUIRED", "This order has no amount to collect.")
    return order, policy


async def record_manual_collection_attempt(
    db: AsyncSession, org: Organization, actor: PublicUser, *,
    customer_ref: UUID, attempt_reference: UUID,
) -> StudyPaymentAttempt:
    """Retain a pending staff-led attempt in the caller's transaction."""
    order, policy = await _manual_collection_context(db, org, actor, customer_ref)
    attempt_ref = str(attempt_reference)
    existing = (await db.execute(select(StudyPaymentAttempt).where(
        StudyPaymentAttempt.org_id == org.id, StudyPaymentAttempt.method == "manual",
        StudyPaymentAttempt.attempt_reference == attempt_ref,
    ))).scalar_one_or_none()
    if existing is not None:
        if existing.order_id != order.id:
            raise studytomy_http_error(409, "COLLECTION_ATTEMPT_CONFLICT", "The attempt reference is already assigned.")
        return existing
    attempt = StudyPaymentAttempt(
        org_id=org.id, order_id=order.id, method="manual", attempt_reference=attempt_ref,
    )
    db.add(attempt)
    await append_business_event(
        db, org_id=org.id, actor_user_id=actor.id, resource_kind="collection_attempt",
        resource_uuid=attempt_ref, action="studytomy.collection.attempt_recorded",
        reason_code="COLLECTION_ATTEMPT_RECORDED", policy_refs=[int(policy.id)],
        after_state={"status": "pending"}, dedupe_key=f"studytomy:collection:attempt:{org.id}:{attempt_ref}",
    )
    await db.flush()
    return attempt


async def attest_manual_bank_evidence(
    db: AsyncSession, org: Organization, actor: PublicUser, *, customer_ref: UUID,
    attempt_reference: UUID, command: ManualBankEvidence,
) -> StudyManualCollectionEvidence:
    """Record the assigned operator's review of a settled bank record."""
    order, policy = await _manual_collection_context(db, org, actor, customer_ref)
    attempt = await _manual_attempt(db, org.id, order.id, attempt_reference)
    now = datetime.now(UTC)
    if command.settled_at > now:
        raise studytomy_http_error(422, "BANK_SETTLEMENT_IN_FUTURE", "The bank settlement cannot be in the future.")
    existing = (await db.execute(select(StudyManualCollectionEvidence).where(
        StudyManualCollectionEvidence.org_id == org.id,
        StudyManualCollectionEvidence.external_reference == command.external_reference,
    ))).scalar_one_or_none()
    if existing is not None:
        fields = command.model_dump()
        if existing.attempt_id == attempt.id and existing.verified_by_user_id == actor.id and all(
            getattr(existing, key) == value
            for key, value in fields.items() if key != "settled_at"
        ) and existing.settled_at.replace(tzinfo=existing.settled_at.tzinfo or UTC) == command.settled_at:
            return existing
        raise studytomy_http_error(409, "BANK_EVIDENCE_REFERENCE_CONFLICT", "The bank reference is already recorded.")
    if await db.get(StudyManualCollectionEvidence, attempt.id) is not None:
        raise studytomy_http_error(409, "BANK_EVIDENCE_ALREADY_RECORDED", "This attempt already retains bank evidence.")
    evidence = StudyManualCollectionEvidence(
        **command.model_dump(), org_id=org.id, order_id=order.id, attempt_id=attempt.id,
        verified_by_user_id=actor.id, verified_at=now,
    )
    db.add(evidence)
    await append_business_event(
        db, org_id=org.id, actor_user_id=actor.id, resource_kind="collection_attempt",
        resource_uuid=str(attempt_reference), action="studytomy.collection.bank_evidence_attested",
        reason_code="BANK_EVIDENCE_ATTESTED", policy_refs=[int(policy.id)],
        after_state={"status": "evidence_attested", "source_snapshot_digest": command.record_sha256},
        dedupe_key=f"studytomy:collection:evidence:{org.id}:{attempt_reference}",
    )
    await db.flush()
    return evidence


async def _manual_attempt(
    db: AsyncSession, org_id: int, order_id: int, attempt_reference: UUID,
) -> StudyPaymentAttempt:
    attempt = (await db.execute(select(StudyPaymentAttempt).where(
        StudyPaymentAttempt.org_id == org_id, StudyPaymentAttempt.order_id == order_id,
        StudyPaymentAttempt.method == "manual", StudyPaymentAttempt.attempt_reference == str(attempt_reference),
    ))).scalar_one_or_none()
    if attempt is None:
        raise studytomy_http_error(404, "COLLECTION_ATTEMPT_UNAVAILABLE", "The collection attempt is unavailable.")
    return attempt


async def _collection_capacity_available(
    db: AsyncSession, org: Organization, order: StudyOrder, evidence: StudyManualCollectionEvidence,
) -> bool:
    versions = (await db.execute(
        select(StudyOrderLine.offer_id, StudyOrderLine.offer_version_no)
        .where(StudyOrderLine.order_id == order.id, StudyOrderLine.org_id == org.id)
        .distinct()
    )).all()
    if len(versions) != 1:
        return False
    offer_id, version_no = versions[0]
    offer, version = (await db.execute(
        select(StudyOffer, StudyOfferVersion)
        .join(StudyOfferVersion, StudyOfferVersion.offer_id == StudyOffer.id)
        .where(StudyOffer.org_id == org.id, StudyOffer.id == offer_id,
               StudyOfferVersion.version_no == version_no)
    )).one()
    terms = StudyOfferTerms.model_validate(version.terms)
    try:
        await require_pilot_checkout(
            db, org, offer_uuid=offer.offer_uuid, offer_version_uuid=version.version_uuid,
        )
        if terms.capacity_is_limited is True:
            hold = (await db.execute(select(StudyCapacityHold.id).where(
                StudyCapacityHold.offer_id == offer_id, StudyCapacityHold.request_uuid == order.customer_ref,
                StudyCapacityHold.created_at <= evidence.settled_at,
                StudyCapacityHold.expires_at > evidence.settled_at,
            ))).scalar_one_or_none()
            if hold is None:
                return False
            await consume_offer_capacity(
                db, org_id=org.id, offer_id=offer_id, request_uuid=order.customer_ref, sales_open=True,
            )
        return terms.capacity_is_limited is not None
    except HTTPException as error:
        if error.status_code != 409:
            raise
        return False


async def confirm_manual_collection(
    db: AsyncSession, org: Organization, actor: PublicUser, *,
    customer_ref: UUID, attempt_reference: UUID,
) -> StudyMoneyMovement | StudyCollectionRemedy:
    """Confirm the exact retained bank record without granting learner access."""
    order, policy = await _manual_collection_context(db, org, actor, customer_ref)
    attempt = await _manual_attempt(db, org.id, order.id, attempt_reference)
    remedy = await db.get(StudyCollectionRemedy, attempt.id)
    if remedy is not None:
        return remedy
    related = (await db.execute(select(StudyMoneyMovement).where(
        StudyMoneyMovement.org_id == org.id, StudyMoneyMovement.order_id == order.id,
    ))).scalars().all()
    for movement in related:
        if movement.attempt_id == attempt.id:
            return movement
    evidence = await db.get(StudyManualCollectionEvidence, attempt.id)
    if evidence is None:
        raise studytomy_http_error(409, "BANK_EVIDENCE_REQUIRED", "Verified bank evidence is required.")
    reason = None
    if related:
        reason = "additional_collection"
    elif evidence.amount_minor != order.total_minor or evidence.currency != order.currency:
        reason = "amount_or_currency_mismatch"
    else:
        body, _ = inspect_study_policy_body(StudyPolicyKey.D_19, policy.body)
        authority = body.specified_value("staff_authority") if body else None
        limits = authority.get("manual_collection_limits_minor") if isinstance(authority, dict) else None
        limit = limits.get(evidence.currency) if isinstance(limits, dict) else None
        if type(limit) is not int or evidence.amount_minor > limit:
            raise studytomy_http_error(409, "COLLECTION_LIMIT_NOT_APPROVED", "Finance approval does not cover this amount.")
        if not await _collection_capacity_available(db, org, order, evidence):
            reason = "capacity_or_sale_unavailable"
    if reason is not None:
        remedy = StudyCollectionRemedy(
            attempt_id=attempt.id, org_id=org.id, order_id=order.id, opened_by_user_id=actor.id,
            reason=reason,
        )
        db.add(remedy)
        await append_business_event(
            db, org_id=org.id, actor_user_id=actor.id, resource_kind="collection_attempt",
            resource_uuid=str(attempt_reference), action="studytomy.collection.remedy_opened",
            reason_code="COLLECTION_REMEDY_REVIEW_REQUIRED", policy_refs=[int(policy.id)],
            after_state={"status": "remedy_review", "source_snapshot_digest": evidence.record_sha256},
            dedupe_key=f"studytomy:collection:remedy:{org.id}:{attempt_reference}",
        )
        await db.flush()
        return remedy
    movement = StudyMoneyMovement(
        org_id=org.id, order_id=order.id, attempt_id=attempt.id,
        amount_minor=evidence.amount_minor, currency=evidence.currency,
        external_reference=evidence.external_reference, evidence_reference=evidence.evidence_reference,
        confirmed_by_user_id=actor.id,
    )
    db.add(movement)
    await append_business_event(
        db, org_id=org.id, actor_user_id=actor.id, resource_kind="collection_attempt",
        resource_uuid=str(attempt_reference), action="studytomy.collection.confirmed",
        reason_code="COLLECTION_CONFIRMED", policy_refs=[int(policy.id)],
        after_state={"status": "confirmed", "source_snapshot_digest": evidence.record_sha256},
        dedupe_key=f"studytomy:collection:confirmed:{org.id}:{attempt_reference}",
    )
    await db.flush()
    return movement


async def allocate_confirmed_order(
    db: AsyncSession, org: Organization, actor: PublicUser, *,
    customer_ref: UUID, attempt_reference: UUID,
) -> list[StudyCourseAccessGrant]:
    """Allocate the original order lines against a retained confirmed collection."""
    order, finance_policy = await _manual_collection_context(db, org, actor, customer_ref)
    ended = (await db.execute(select(StudyBusinessEvent.id).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.subject_uuid == str(customer_ref),
        StudyBusinessEvent.action == "studytomy.refund.settled",
        StudyBusinessEvent.after_state["refund_access_effect"].as_string() == "order_access_ended",
    ).limit(1))).scalar_one_or_none()
    if ended is not None:
        raise studytomy_http_error(409, "ORDER_ACCESS_ENDED", "This order no longer authorizes course access.")
    attempt = await _manual_attempt(db, org.id, order.id, attempt_reference)
    movement = (await db.execute(select(StudyMoneyMovement).where(
        StudyMoneyMovement.org_id == org.id, StudyMoneyMovement.order_id == order.id,
        StudyMoneyMovement.attempt_id == attempt.id,
    ))).scalar_one_or_none()
    evidence = await db.get(StudyManualCollectionEvidence, attempt.id)
    if (
        movement is None or evidence is None or movement.amount_minor != order.total_minor
        or movement.currency != order.currency
    ):
        raise studytomy_http_error(409, "COLLECTION_CONFIRMATION_REQUIRED", "Confirmed collection is required.")
    return await _allocate_accepted_lines(
        db, org, order, actor.id, start_at=evidence.settled_at, finance_policy=finance_policy,
    )


async def allocate_approved_free_order(
    db: AsyncSession, org: Organization, acceptance: StudyOrderAcceptance,
) -> None:
    """Materialize a separately approved free allocation in the acceptance transaction."""
    order = await db.get(StudyOrder, acceptance.order_id)
    if order is None or order.org_id != org.id or order.purchaser_user_id != acceptance.purchaser_user_id:
        raise studytomy_http_error(409, "ORDER_ALLOCATION_UNAVAILABLE", "The accepted order is unavailable.")
    if order.total_minor != 0:
        return
    await _allocate_accepted_lines(
        db, org, order, acceptance.purchaser_user_id, start_at=acceptance.accepted_at,
    )


async def _allocate_accepted_lines(
    db: AsyncSession, org: Organization, order: StudyOrder, actor_id: int, *,
    start_at: datetime, finance_policy: StudyPolicyVersion | None = None,
) -> list[StudyCourseAccessGrant]:
    now = datetime.now(UTC)
    policies = await _current_policies(db, org, (
        StudyPolicyKey.D_01, StudyPolicyKey.D_03, StudyPolicyKey.D_05, StudyPolicyKey.D_07,
    ), now)
    pricing = next(policy for policy in policies if policy.policy_key == StudyPolicyKey.D_05.value)
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_05, pricing.body)
    allocation = body.specified_value("learner_allocation") if body else None
    approved_versions = allocation.get("order_line_offer_versions") if isinstance(allocation, dict) else None
    periods = body.specified_value("access_periods") if body else None
    lines = (await db.execute(
        select(StudyOrderLine, StudyOffer, StudyOfferVersion)
        .select_from(StudyOrderLine)
        .join(StudyOffer, StudyOffer.id == StudyOrderLine.offer_id)
        .join(StudyOfferVersion, (StudyOfferVersion.offer_id == StudyOffer.id)
              & (StudyOfferVersion.version_no == StudyOrderLine.offer_version_no))
        .where(StudyOrderLine.org_id == org.id, StudyOrderLine.order_id == order.id)
        .order_by(StudyOrderLine.id)
    )).all()
    if not lines:
        raise studytomy_http_error(409, "ORDER_ALLOCATION_UNAVAILABLE", "The accepted order has no named allocations.")
    grants = []
    for line, offer, version in lines:
        terms = StudyOfferTerms.model_validate(line.terms_snapshot)
        access = terms.access
        if (
            not isinstance(approved_versions, dict) or approved_versions.get(offer.offer_uuid) != version.version_uuid
            or not isinstance(periods, dict) or access is None or access.clock is None
            or periods.get(offer.offer_uuid) != access.model_dump(mode="json")
            or terms.completion_blockers()
        ):
            raise studytomy_http_error(409, "ORDER_ALLOCATION_NOT_APPROVED", "The accepted allocation rules are unavailable.")
        if finance_policy is None:
            if (
                line.total_minor != 0 or terms.price is None or terms.price.total_minor != 0
                or line.terms_snapshot != version.terms
            ):
                raise studytomy_http_error(409, "FREE_ORDER_TERMS_CHANGED", "The accepted free terms are unavailable.")
            require_free_order_version(body, offer.offer_uuid, version.version_uuid)
            await require_pilot_checkout(
                db, org, offer_uuid=offer.offer_uuid, offer_version_uuid=version.version_uuid,
            )
        if not await can_purchase_for_learner(
            db, org, purchaser_id=order.purchaser_user_id, learner_profile_id=line.learner_profile_id, now=now,
        ):
            raise studytomy_http_error(409, "ORDER_LEARNER_AUTHORITY_CHANGED", "The allocation requires authority review.")
        if terms.capacity_is_limited and finance_policy is None:
            await consume_offer_capacity(
                db, org_id=org.id, offer_id=offer.id, request_uuid=order.customer_ref, sales_open=True,
            )
        elif terms.capacity_is_limited:
            consumed = (await db.execute(select(StudyCapacityHold.id).where(
                StudyCapacityHold.offer_id == offer.id, StudyCapacityHold.request_uuid == order.customer_ref,
                StudyCapacityHold.consumed_at.is_not(None),
            ))).scalar_one_or_none()
            if consumed is None:
                raise studytomy_http_error(409, "ORDER_CAPACITY_NOT_CONSUMED", "The allocation has no consumed place.")
        current = await _snapshot_resource_titles(db, org, terms, require_current_release_policies_at=now)
        if current != terms:
            raise studytomy_http_error(409, "ORDER_RELEASE_CHANGED", "The accepted course release is unavailable.")
        start, end, duration = access.starts_at, access.ends_at, None
        if access.clock == StudyOfferAccessClock.AFTER_PURCHASE:
            start = start_at
            end = start + timedelta(days=access.duration_days)
        elif access.clock == StudyOfferAccessClock.AFTER_FIRST_ACCESS:
            duration = access.duration_days
        for resource in terms.resources or []:
            release_id = (await db.execute(select(StudyCourseRelease.id).where(
                StudyCourseRelease.org_id == org.id, StudyCourseRelease.course_uuid == resource.resource_uuid,
                StudyCourseRelease.uuid == resource.release_uuid,
            ))).scalar_one()
            grants.append(StudyCourseAccessGrant(
                org_id=org.id, learner_profile_id=line.learner_profile_id, course_uuid=resource.resource_uuid,
                release_id=release_id, source_kind="order_line", source_uuid=str(line.id),
                start_basis=access.clock.value, starts_at=start, ends_at=end, duration_days=duration,
            ))
    delivery_policy = await current_allocation_delivery_policy(db, org, now)
    event_policies = [policy.id for policy in policies]
    if finance_policy is not None:
        event_policies.append(finance_policy.id)
    if delivery_policy is not None:
        event_policies.append(delivery_policy.id)
    saved = []
    for candidate in grants:
        grant = await persist_authorized_course_grant(db, candidate)
        if grant is candidate:
            await append_business_event(
                db, org_id=org.id, actor_user_id=actor_id, resource_kind="course_access_grant",
                resource_uuid=str(grant.id), action="studytomy.course_grant.issued",
                reason_code="ORDER_LINE_GRANT_ISSUED",
                policy_refs=event_policies,
                after_state={"status": "issued"}, dedupe_key=f"studytomy:course_grant:{org.id}:{grant.id}",
            )
        saved.append(grant)
    return saved


async def resolve_manual_collection_remedy(
    db: AsyncSession, org: Organization, actor: PublicUser, *, customer_ref: UUID,
    attempt_reference: UUID, command: ManualRemedyResolution,
) -> StudyCollectionResolution:
    """Attest an already completed external outcome without performing a refund."""
    order, finance = await _manual_collection_context(db, org, actor, customer_ref)
    attempt = await _manual_attempt(db, org.id, order.id, attempt_reference)
    remedy = await db.get(StudyCollectionRemedy, attempt.id)
    if remedy is None:
        raise studytomy_http_error(404, "COLLECTION_REMEDY_UNAVAILABLE", "The collection review is unavailable.")
    now = datetime.now(UTC)
    remedies = await _active_policy(db, org, StudyPolicyKey.D_07, now)
    finance_body, _ = inspect_study_policy_body(StudyPolicyKey.D_19, finance.body)
    remedy_body, _ = inspect_study_policy_body(StudyPolicyKey.D_07, remedies.body)
    authority = finance_body.specified_value("staff_authority") if finance_body else None
    rules = remedy_body.specified_value("withdrawal_remedies") if remedy_body else None
    outcomes = rules.get("manual_collection_outcomes") if isinstance(rules, dict) else None
    if (
        not isinstance(authority, dict) or authority.get("manual_remedy_resolution_enabled") is not True
        or not isinstance(outcomes, list) or command.outcome not in outcomes
    ):
        raise studytomy_http_error(409, "COLLECTION_REMEDY_NOT_APPROVED", "Current finance policy does not approve this outcome.")
    evidence = await db.get(StudyManualCollectionEvidence, attempt.id)
    if authority.get("manual_remedy_independent_reviewer") is True and actor.id in (
        remedy.opened_by_user_id, evidence.verified_by_user_id,
    ):
        raise studytomy_http_error(403, "COLLECTION_CONFLICT_OF_INTEREST", "Another operator must resolve this review.")
    existing = await db.get(StudyCollectionResolution, attempt.id)
    if existing is not None:
        if any(getattr(existing, field) != value for field, value in command.model_dump().items()):
            raise studytomy_http_error(409, "COLLECTION_RESOLUTION_CONFLICT", "The recorded resolution differs.")
        return existing
    resolution = StudyCollectionResolution(
        **command.model_dump(), attempt_id=attempt.id, resolved_by_user_id=actor.id, resolved_at=now,
    )
    db.add(resolution)
    await append_business_event(
        db, org_id=org.id, actor_user_id=actor.id, resource_kind="collection_attempt",
        resource_uuid=str(attempt_reference), action="studytomy.collection.remedy_resolved",
        reason_code="COLLECTION_REMEDY_RESOLVED", policy_refs=[int(remedies.id), int(finance.id)],
        after_state={"status": command.outcome, "source_snapshot_digest": command.record_sha256},
        dedupe_key=f"studytomy:collection:resolution:{org.id}:{attempt_reference}",
    )
    await db.flush()
    return resolution
