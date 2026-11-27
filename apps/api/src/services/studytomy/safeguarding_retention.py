"""Metadata-only retention inventory for confidential safeguarding case evidence."""

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.cases import StudySafeguardingNarrative, StudyServiceCase, StudyServiceCaseExclusion
from src.db.studytomy.governance import StudyBusinessEvent, StudyPolicyKey, StudyPolicyVersion
from src.db.studytomy.operations import StudyEvidenceFile
from src.db.users import PublicUser
from src.security.studytomy_access import StudytomyCapabilityName, evaluate_studytomy_staff_access
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.evidence_files import _policy_area_values
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization
from src.services.studytomy.retention import (
    CaseEvidenceHoldCommand, CaseEvidenceHoldReceipt,
    CaseEvidenceHoldReleaseCommand, CaseEvidenceHoldReleaseReceipt,
)

_HELD = "studytomy.safeguarding.hold_evidence"
_RELEASED = "studytomy.safeguarding.release_evidence_hold"



class SafeguardingRetentionCategory(BaseModel):
    category: str
    narrative_records: int
    file_records: int


class SafeguardingRetentionInventory(BaseModel):
    case_uuid: UUID
    case_status: str
    categories: list[SafeguardingRetentionCategory]
    active_holds: list[UUID]
    hold_reason_codes: list[str]
    release_reason_codes: list[str]


async def _safeguarding_retention_context(
    db: AsyncSession, org: Organization, actor: PublicUser, reference: UUID,
) -> tuple[StudyServiceCase, list[StudyPolicyVersion]]:
    case = (await db.exec(select(StudyServiceCase).where(
        StudyServiceCase.org_id == org.id, StudyServiceCase.case_uuid == str(reference),
        StudyServiceCase.category == "safeguarding",
    ))).one_or_none()
    if case is None:
        raise HTTPException(404, "Case not found.")
    excluded = await db.get(StudyServiceCaseExclusion, (org.id, case.case_uuid, actor.id))
    decision = await evaluate_studytomy_staff_access(
        db, actor, org, StudytomyCapabilityName.PRIVACY_RETENTION_REVIEW,
        target_resource_uuid=case.case_uuid, subject_user_id=case.reporter_user_id,
        excluded_user_ids=(actor.id,) if excluded is not None else (),
        access_version=case.version,
    )
    if not decision.allowed:
        raise HTTPException(404, "Case not found.")
    try:
        policies = await _current_policies(
            db, org, (StudyPolicyKey.D_12, StudyPolicyKey.D_13, StudyPolicyKey.D_15), datetime.now(UTC),
        )
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        raise HTTPException(404, "Case not found.") from None
    if decision.policy_version_ids != [int(policy.id) for policy in policies]:
        raise HTTPException(404, "Case not found.")

    return case, policies


async def _hold_history(db: AsyncSession, org_id: int, case_uuid: str) -> list[StudyBusinessEvent]:
    return list((await db.exec(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org_id, StudyBusinessEvent.subject_uuid == case_uuid,
        StudyBusinessEvent.action.in_((_HELD, _RELEASED)),
    ).order_by(StudyBusinessEvent.id))).all())


async def safeguarding_retention_inventory(
    db: AsyncSession, org: Organization, actor: PublicUser, reference: UUID,
) -> SafeguardingRetentionInventory:
    """Return purpose-bound counts to a privacy reviewer assigned to this case."""
    case, policies = await _safeguarding_retention_context(db, org, actor, reference)
    hold_rules = _policy_area_values(policies[1], "retention").get("safeguarding_evidence_holds")
    market_rules = {}
    try:
        market, = await _current_policies(db, org, (StudyPolicyKey.D_18,), datetime.now(UTC))
        market_rules = _policy_area_values(market, "selected_market_requirements")
    except HTTPException as error:
        if error.status_code != 409:
            raise

    def allowed_reasons(flag: str, field: str) -> list[str]:
        if market_rules.get(flag) is not True or not isinstance(hold_rules, dict):
            return []
        if field == "reason_codes":
            max_days = hold_rules.get("max_review_days")
            if type(max_days) is not int or not 1 <= max_days <= 365:
                return []
        reasons = hold_rules.get(field)
        if not isinstance(reasons, list):
            return []
        return sorted({reason for reason in reasons
                       if isinstance(reason, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason)})
    history = await _hold_history(db, org.id, case.case_uuid)
    released = {event.operation_uuid for event in history if event.action == _RELEASED}

    narrative_category = (await db.exec(select(StudySafeguardingNarrative.retention_category).where(
        StudySafeguardingNarrative.org_id == org.id, StudySafeguardingNarrative.case_uuid == case.case_uuid,
    ))).one_or_none()
    counted_files = (await db.exec(
        select(StudyEvidenceFile.retention_category, func.count(StudyEvidenceFile.id))
        .where(
            StudyEvidenceFile.org_id == org.id, StudyEvidenceFile.safeguarding_case_uuid == case.case_uuid,
            StudyEvidenceFile.purpose == "safeguarding_case_evidence",
        )
        .group_by(StudyEvidenceFile.retention_category)
    )).tuples().all()
    file_counts = dict(counted_files)
    categories = set(file_counts)
    if narrative_category is not None:
        categories.add(narrative_category)
    return SafeguardingRetentionInventory(
        case_uuid=reference, case_status=case.status,
        hold_reason_codes=allowed_reasons("safeguarding_evidence_holds_enabled", "reason_codes"),
        release_reason_codes=allowed_reasons("safeguarding_evidence_hold_release_enabled", "release_reason_codes"),
        active_holds=[
            UUID(event.resource_uuid) for event in history
            if event.action == _HELD and event.event_uuid not in released
        ],
        categories=[
            SafeguardingRetentionCategory(
                category=category, narrative_records=int(category == narrative_category),
                file_records=file_counts.get(category, 0),
            )
            for category in sorted(categories)
        ],
    )


async def _safeguarding_hold_context(
    db: AsyncSession, org: Organization, actor: PublicUser, case_reference: UUID, reason: str,
    *, release: bool,
):
    await lock_organization(db, org.id)
    case, policies = await _safeguarding_retention_context(db, org, actor, case_reference)
    now = datetime.now(UTC)
    market, = await _current_policies(db, org, (StudyPolicyKey.D_18,), now)
    market_rules = _policy_area_values(market, "selected_market_requirements")
    retention_rules = _policy_area_values(policies[1], "retention").get("safeguarding_evidence_holds")
    enabled_key = "safeguarding_evidence_hold_release_enabled" if release else "safeguarding_evidence_holds_enabled"
    reason_key = "release_reason_codes" if release else "reason_codes"
    reasons = retention_rules.get(reason_key) if isinstance(retention_rules, dict) else None
    if market_rules.get(enabled_key) is not True or not isinstance(reasons, list) or reason not in reasons:
        raise HTTPException(409, "The current policy does not approve this hold action.")
    return case, now, retention_rules, [int(policy.id) for policy in (*policies, market)]


async def place_safeguarding_hold(
    db: AsyncSession, org: Organization, actor: PublicUser, case_reference: UUID, hold_reference: UUID,
    command: CaseEvidenceHoldCommand,
) -> CaseEvidenceHoldReceipt:
    case, now, rules, policy_ids = await _safeguarding_hold_context(
        db, org, actor, case_reference, command.reason_code, release=False,
    )
    reviewed_at = command.review_due_at.astimezone(UTC)
    existing = next((event for event in await _hold_history(db, org.id, case.case_uuid)
                     if event.action == _HELD and event.resource_uuid == str(hold_reference)), None)
    data = {"decision_code": command.reason_code, "review_due_at": reviewed_at.isoformat()}
    if existing is not None:
        if existing.after_state != data:
            raise HTTPException(409, "A different hold already uses this reference.")
    else:
        max_days = rules.get("max_review_days")
        if type(max_days) is not int or not 1 <= max_days <= 365 or not now < reviewed_at <= now + timedelta(days=max_days):
            raise HTTPException(409, "The review date must be within the approved window.")
        await append_business_event(
            db, org_id=org.id, actor_user_id=actor.id, action=_HELD, reason_code="SAFEGUARDING_HOLD_RECORDED",
            subject_uuid=case.case_uuid, resource_kind="safeguarding_hold", resource_uuid=str(hold_reference),
            policy_refs=policy_ids, after_state=data,
            dedupe_key=f"studytomy:safeguarding:hold:{org.id}:{case.case_uuid}:{hold_reference}",
        )
    return CaseEvidenceHoldReceipt(hold_reference=hold_reference, review_due_at=reviewed_at)


async def release_safeguarding_hold(
    db: AsyncSession, org: Organization, actor: PublicUser, case_reference: UUID, hold_reference: UUID,
    command: CaseEvidenceHoldReleaseCommand,
) -> CaseEvidenceHoldReleaseReceipt:
    case, _, _, policy_ids = await _safeguarding_hold_context(
        db, org, actor, case_reference, command.reason_code, release=True,
    )
    history = await _hold_history(db, org.id, case.case_uuid)
    hold = next((event for event in history if event.action == _HELD and event.resource_uuid == str(hold_reference)), None)
    if hold is None:
        raise HTTPException(404, "Hold not found.")
    release = next((event for event in history if event.action == _RELEASED and event.operation_uuid == hold.event_uuid), None)
    if release is not None:
        if release.after_state != {"decision_code": command.reason_code}:
            raise HTTPException(409, "A different release is already retained.")
    else:
        release = await append_business_event(
            db, org_id=org.id, actor_user_id=actor.id, action=_RELEASED,
            reason_code="SAFEGUARDING_HOLD_RELEASED", subject_uuid=case.case_uuid,
            resource_kind="safeguarding_hold", resource_uuid=str(hold_reference),
            operation_uuid=hold.event_uuid, policy_refs=policy_ids,
            after_state={"decision_code": command.reason_code},
            dedupe_key=f"studytomy:safeguarding:hold_release:{org.id}:{hold.event_uuid}",
        )
    return CaseEvidenceHoldReleaseReceipt(hold_reference=hold_reference, released_at=release.created_at)
