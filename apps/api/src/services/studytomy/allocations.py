"""Evidence checks for reviewed corrections to named course allocations."""

from datetime import UTC, datetime
from re import fullmatch
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.access import StudyCourseAccessGrant
from src.db.studytomy.collections import (
    StudyChargebackAdjustment,
    StudyRefundReservation,
)
from src.db.studytomy.commerce import StudyOfferTerms
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyPolicyKey,
    StudyPolicyVersion,
    inspect_study_policy_body,
)
from src.db.studytomy.orders import StudyOrder, StudyOrderAcceptance, StudyOrderLine
from src.db.users import PublicUser
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_studytomy_staff_action,
)
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.entitlements import (
    current_allocation_delivery_policy,
    persist_authorized_course_grant,
)
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.offers import _snapshot_resource_titles
from src.services.studytomy.orders import can_purchase_for_learner
from src.services.studytomy.policies import lock_organization


class AllocationCorrectionReview(BaseModel):
    status: Literal["used", "unstarted", "evidence_unavailable"]
    reason_codes: list[str] = Field(default_factory=list)


class AllocationCorrectionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replacement_learner_uuid: str = Field(min_length=1, max_length=100)
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


class AllocationCorrectionReceipt(BaseModel):
    correction_uuid: UUID
    status: Literal["corrected"] = "corrected"


async def read_allocation_correction_review(
    db: AsyncSession, org: Organization, actor: PublicUser, learner_uuid: str, customer_ref: UUID,
) -> AllocationCorrectionReview:
    """Return the supported evidence decision for the exact learner and order."""
    org = await lock_organization(db, org.id)
    now = datetime.now(UTC)
    _, _, grants, _, pricing = await _allocation_review_context(db, org, actor, learner_uuid, customer_ref, now)
    review = await _allocation_usage(db, org.id, grants)
    if review.status == "unstarted":
        review.reason_codes = _correction_reason_codes(pricing)
    return review


def _correction_reason_codes(pricing: dict) -> list[str]:
    reasons = pricing.get("correction_reason_codes")
    if not isinstance(reasons, list):
        return []
    return list(dict.fromkeys(
        reason for reason in reasons if isinstance(reason, str) and fullmatch(r"[a-z][a-z0-9_]{0,63}", reason)
    ))


async def _allocation_review_context(
    db: AsyncSession, org: Organization, actor: PublicUser, learner_uuid: str, customer_ref: UUID, now: datetime,
) -> tuple[StudyOrder, StudyOrderLine, list[StudyCourseAccessGrant], list[StudyPolicyVersion], dict]:
    await authorize_studytomy_staff_action(
        db, actor, org, StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW,
        target_scope_kind="learner", target_resource_uuid=learner_uuid,
        hide_existence_on_denial=True, now=now,
    )
    lines = (await db.execute(
        select(StudyOrderLine, StudyOrder)
        .join(StudyOrder, StudyOrder.id == StudyOrderLine.order_id)
        .join(StudyOrderAcceptance, StudyOrderAcceptance.order_id == StudyOrder.id)
        .join(StudyLearnerProfile, StudyLearnerProfile.id == StudyOrderLine.learner_profile_id)
        .where(
            StudyOrder.org_id == org.id, StudyOrder.customer_ref == str(customer_ref),
            StudyOrderLine.org_id == org.id, StudyLearnerProfile.org_id == org.id,
            StudyLearnerProfile.learner_uuid == learner_uuid,
        )
    )).all()
    if len(lines) != 1:
        raise studytomy_http_error(404, "ALLOCATION_UNAVAILABLE", "The allocation is unavailable.")
    line, order = lines[0]
    family, pricing = await _current_policies(db, org, (StudyPolicyKey.D_03, StudyPolicyKey.D_05), now)
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_05, pricing.body)
    policy = body.specified_value("learner_allocation") if body else None
    information = await current_allocation_delivery_policy(db, org, now)
    if (
        not isinstance(policy, dict) or policy.get("substantive_use_rule") != "first_content_delivery"
        or information is None
    ):
        raise studytomy_http_error(409, "ALLOCATION_REVIEW_NOT_APPROVED", "Current policy does not approve this allocation review.")
    grant = StudyCourseAccessGrant
    grants = (await db.execute(select(grant).where(
        grant.org_id == org.id, grant.learner_profile_id == line.learner_profile_id,
        grant.source_kind == "order_line", grant.source_uuid == str(line.id),
    ).order_by(grant.id).execution_options(populate_existing=True))).scalars().all()
    if not grants:
        raise studytomy_http_error(404, "ALLOCATION_UNAVAILABLE", "The allocation is unavailable.")
    return order, line, list(grants), [family, pricing, information], policy


async def _allocation_usage(
    db: AsyncSession, org_id: int, grants: list[StudyCourseAccessGrant],
) -> AllocationCorrectionReview:
    event = StudyBusinessEvent
    delivery = (await db.execute(select(event.id).where(
        event.org_id == org_id, event.action == "studytomy.course_grant.content_delivered",
        event.resource_kind == "course_access_grant", event.resource_uuid.in_([str(row.id) for row in grants]),
    ).limit(1))).scalar_one_or_none()
    if delivery is not None:
        return AllocationCorrectionReview(status="used")
    if all(row.start_basis == "after_first_access" and row.starts_at is None and row.revoked_at is None for row in grants):
        return AllocationCorrectionReview(status="unstarted")
    return AllocationCorrectionReview(status="evidence_unavailable")


async def correct_unstarted_allocation(
    db: AsyncSession, org: Organization, actor: PublicUser, learner_uuid: str,
    customer_ref: UUID, command: AllocationCorrectionCommand,
) -> AllocationCorrectionReceipt:
    org = await lock_organization(db, org.id)
    now = datetime.now(UTC)
    order, line, grants, policies, pricing = await _allocation_review_context(
        db, org, actor, learner_uuid, customer_ref, now,
    )
    if command.reason_code not in _correction_reason_codes(pricing):
        raise studytomy_http_error(409, "ALLOCATION_CORRECTION_NOT_APPROVED", "Current policy does not approve this correction.")
    if command.replacement_learner_uuid == learner_uuid or actor.id == order.purchaser_user_id:
        raise studytomy_http_error(409, "ALLOCATION_CORRECTION_CONFLICT", "A different eligible learner and independent reviewer are required.")
    await authorize_studytomy_staff_action(
        db, actor, org, StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW,
        target_scope_kind="learner", target_resource_uuid=command.replacement_learner_uuid,
        hide_existence_on_denial=True, now=now,
    )
    key = f"studytomy:allocation:corrected:{org.id}:{line.id}"
    digest = canonical_request_hash(command)
    previous = (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.dedupe_key == key,
    ))).scalar_one_or_none()
    if previous is not None:
        if (previous.after_state or {}).get("source_snapshot_digest") != digest:
            raise studytomy_http_error(409, "ALLOCATION_ALREADY_CORRECTED", "A different correction is already retained.")
        return AllocationCorrectionReceipt(correction_uuid=previous.event_uuid)
    if (await _allocation_usage(db, org.id, grants)).status != "unstarted":
        raise studytomy_http_error(409, "ALLOCATION_EXCEPTION_REVIEW_REQUIRED", "This allocation requires exception review.")
    replacement = (await db.execute(select(StudyLearnerProfile).where(
        StudyLearnerProfile.org_id == org.id,
        StudyLearnerProfile.learner_uuid == command.replacement_learner_uuid,
    ))).scalar_one_or_none()
    if replacement is None or actor.id == replacement.user_id or not await can_purchase_for_learner(
        db, org, purchaser_id=order.purchaser_user_id, learner_profile_id=replacement.id, now=now,
    ):
        raise studytomy_http_error(404, "ALLOCATION_LEARNER_UNAVAILABLE", "The replacement learner is unavailable.")
    duplicate = (await db.execute(select(StudyOrderLine.id).where(
        StudyOrderLine.org_id == org.id, StudyOrderLine.order_id == order.id,
        StudyOrderLine.learner_profile_id == replacement.id,
    ).limit(1))).scalar_one_or_none()
    if duplicate is not None:
        raise studytomy_http_error(409, "ALLOCATION_LEARNER_ALREADY_NAMED", "The replacement learner already has a place.")
    for ledger in (StudyRefundReservation, StudyChargebackAdjustment):
        review_required = (await db.execute(select(ledger.id).where(
            ledger.org_id == org.id, ledger.order_id == order.id,
        ).limit(1))).scalar_one_or_none()
        if review_required is not None:
            raise studytomy_http_error(409, "ALLOCATION_FINANCE_REVIEW_REQUIRED", "The order requires financial review.")
    terms = StudyOfferTerms.model_validate(line.terms_snapshot)
    if await _snapshot_resource_titles(db, org, terms, require_current_release_policies_at=now) != terms:
        raise studytomy_http_error(409, "ORDER_RELEASE_CHANGED", "The accepted course release is unavailable.")
    event = await append_business_event(
        db, org_id=org.id, actor_user_id=actor.id, subject_uuid=learner_uuid,
        resource_kind="order_line", resource_uuid=str(line.id),
        action="studytomy.allocation.corrected", reason_code="UNSTARTED_ALLOCATION_CORRECTED",
        policy_refs=[policy.id for policy in policies],
        after_state={"status": "corrected", "decision_code": command.reason_code, "source_snapshot_digest": digest},
        dedupe_key=key,
    )
    for old in grants:
        old.revoked_at = now
        replacement_grant = await persist_authorized_course_grant(db, StudyCourseAccessGrant(
            org_id=org.id, learner_profile_id=replacement.id, course_uuid=old.course_uuid,
            release_id=old.release_id, source_kind=old.source_kind, source_uuid=old.source_uuid,
            start_basis=old.start_basis, duration_days=old.duration_days, created_at=now,
        ))
        await append_business_event(
            db, org_id=org.id, actor_user_id=actor.id, subject_uuid=replacement.learner_uuid,
            resource_kind="course_access_grant", resource_uuid=str(replacement_grant.id),
            action="studytomy.course_grant.replaced", reason_code="ALLOCATION_GRANT_REPLACED",
            policy_refs=[policy.id for policy in policies], operation_uuid=event.event_uuid,
            after_state={"status": "issued", "predecessor_grant_id": old.id},
        )
    return AllocationCorrectionReceipt(correction_uuid=event.event_uuid)
