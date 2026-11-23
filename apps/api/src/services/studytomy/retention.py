"""Reviewed holds on private record-request evidence."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyPolicyKey,
    inspect_study_policy_body,
)
from src.db.studytomy.operations import StudyEvidenceFile
from src.db.users import PublicUser
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization
from src.services.studytomy.record_delivery import _HANDED_OFF
from src.services.studytomy.record_requests import (
    _retained_decision,
    _staff_request_context,
)

_HELD = "studytomy.retention.case_evidence_held"
_RELEASED = "studytomy.retention.case_evidence_hold_released"
_REMOVAL_APPROVED = "studytomy.retention.case_evidence_removal_approved"
_REMOVED = "studytomy.retention.case_evidence_removed"


class CaseEvidenceHoldCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    review_due_at: AwareDatetime


class CaseEvidenceHoldReceipt(BaseModel):
    hold_reference: UUID
    review_due_at: datetime


class CaseEvidenceHoldReleaseCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


class CaseEvidenceHoldReleaseReceipt(BaseModel):
    hold_reference: UUID
    released_at: datetime


class EvidenceCandidate(BaseModel):
    evidence_reference: str
    disposition: Literal["held", "case_open", "unverified", "retained", "candidate"]
    eligible_at: datetime | None


class EvidenceDryRun(BaseModel):
    request_reference: UUID
    as_of: datetime
    hold_references: list[UUID]
    files: list[EvidenceCandidate]
    snapshot_sha256: str
    completed_removals: dict[str, UUID]


class EvidenceRemovalApprovalCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: AwareDatetime
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


class EvidenceRemovalApprovalReceipt(BaseModel):
    approval_reference: UUID
    snapshot_sha256: str
    candidate_count: int
    approved_at: datetime


async def approve_case_evidence_removal(
    db: AsyncSession, org: Organization, actor: PublicUser, case_reference: UUID,
    approval_reference: UUID, command: EvidenceRemovalApprovalCommand,
) -> EvidenceRemovalApprovalReceipt:
    # The dry run takes the organization lock and keeps it through approval.
    manifest = await dry_run_case_evidence_retention(db, org, actor, case_reference, command.as_of)
    now = datetime.now(UTC)
    retention, market = await _current_policies(db, org, (StudyPolicyKey.D_13, StudyPolicyKey.D_18), now)
    market_body, _ = inspect_study_policy_body(StudyPolicyKey.D_18, market.body)
    market_rules = market_body.specified_value("selected_market_requirements") if market_body else None
    retention_body, _ = inspect_study_policy_body(StudyPolicyKey.D_13, retention.body)
    removal = retention_body.specified_value("removal") if retention_body else None
    reasons = removal.get("case_evidence_approval_reason_codes") if isinstance(removal, dict) else None
    if (
        not isinstance(market_rules, dict) or market_rules.get("case_evidence_removal_approval_enabled") is not True
        or not isinstance(reasons, list) or command.reason_code not in reasons
    ):
        raise studytomy_http_error(409, "RETENTION_REMOVAL_NOT_APPROVED", "Current policy does not approve removal.")
    if manifest.snapshot_sha256 != command.snapshot_sha256:
        raise studytomy_http_error(409, "RETENTION_MANIFEST_CHANGED", "Review a new dry-run manifest before approval.")
    request, _, _ = await _staff_request_context(db, org, actor, case_reference)
    key = f"studytomy:retention:case_removal_approval:{org.id}:{approval_reference}"
    existing = (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.action == _REMOVAL_APPROVED,
        StudyBusinessEvent.dedupe_key == key,
    ))).scalar_one_or_none()
    if existing is not None:
        if (
            existing.operation_uuid != request.event_uuid or existing.actor_user_id != actor.id
            or (existing.after_state or {}).get("source_snapshot_digest") != manifest.snapshot_sha256
            or (existing.after_state or {}).get("decision_code") != command.reason_code
        ):
            raise studytomy_http_error(409, "RETENTION_APPROVAL_REFERENCE_CONFLICT", "The approval reference is already in use.")
        event = existing
    else:
        candidate_count = sum(
            file.disposition == "candidate" and file.evidence_reference not in manifest.completed_removals
            for file in manifest.files
        )
        if not candidate_count:
            raise studytomy_http_error(409, "RETENTION_NO_CANDIDATES", "This manifest has no eligible removal candidates.")
        event = await append_business_event(
            db, org_id=org.id, actor_user_id=actor.id, action=_REMOVAL_APPROVED,
            reason_code="CASE_EVIDENCE_REMOVAL_APPROVED", subject_uuid=request.subject_uuid,
            resource_kind="record_request_removal", resource_uuid=str(approval_reference),
            operation_uuid=request.event_uuid, dedupe_key=key, policy_refs=sorted([retention.id, market.id]),
            after_state={"status": "approved", "decision_code": command.reason_code,
                         "source_snapshot_digest": manifest.snapshot_sha256,
                         "retention_cutoff": manifest.as_of.isoformat(), "candidate_count": candidate_count},
        )
    return EvidenceRemovalApprovalReceipt(
        approval_reference=approval_reference, snapshot_sha256=manifest.snapshot_sha256,
        candidate_count=event.after_state["candidate_count"], approved_at=event.created_at,
    )


async def dry_run_case_evidence_retention(
    db: AsyncSession, org: Organization, actor: PublicUser, case_reference: UUID, as_of: datetime,
) -> EvidenceDryRun:
    org = await lock_organization(db, org.id)
    request, policies, authority_refs = await _staff_request_context(db, org, actor, case_reference)
    now = datetime.now(UTC)
    as_of = as_of.astimezone(UTC)
    if as_of > now:
        raise studytomy_http_error(422, "RETENTION_AS_OF_INVALID", "The dry-run date cannot be in the future.")
    market, = await _current_policies(db, org, (StudyPolicyKey.D_18,), now)
    market_body, _ = inspect_study_policy_body(StudyPolicyKey.D_18, market.body)
    market_rules = market_body.specified_value("selected_market_requirements") if market_body else None
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_13, policies[2].body)
    retention = body.specified_value("retention") if body else None
    configured = retention.get("case_evidence_retention_days") if isinstance(retention, dict) else None
    category = retention.get("record_request_evidence_retention_category") if isinstance(retention, dict) else None
    if (
        not isinstance(market_rules, dict) or market_rules.get("case_evidence_dry_run_enabled") is not True
        or type(configured) is not int or not 1 <= configured <= 36500
        or not isinstance(category, str) or not category
    ):
        raise studytomy_http_error(409, "RETENTION_DRY_RUN_NOT_APPROVED", "Current policy does not approve this dry run.")
    holds = (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.action == _HELD,
        StudyBusinessEvent.operation_uuid == request.event_uuid,
    ).order_by(StudyBusinessEvent.resource_uuid))).scalars().all()
    releases = []
    if holds:
        releases = (await db.execute(select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.action == _RELEASED,
            StudyBusinessEvent.operation_uuid.in_([hold.event_uuid for hold in holds]),
        ).order_by(StudyBusinessEvent.id))).scalars().all()
    released_event_ids = {release.operation_uuid for release in releases}
    active_holds = [hold.resource_uuid for hold in holds if hold.event_uuid not in released_event_ids]
    decision = await _retained_decision(db, org.id, f"studytomy:record_request:decision:{org.id}:{case_reference}")
    closed_at = None
    if decision and (decision.after_state or {}).get("status") == "denied":
        closed_at = decision.created_at
    else:
        release = (await db.execute(select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.resource_uuid == str(case_reference),
            StudyBusinessEvent.subject_uuid == request.subject_uuid, StudyBusinessEvent.action == _HANDED_OFF,
            StudyBusinessEvent.actor_user_id == request.actor_user_id,
        ))).scalar_one_or_none()
        if release:
            closed_at = release.created_at
    files = (await db.execute(select(StudyEvidenceFile).where(
        StudyEvidenceFile.org_id == org.id, StudyEvidenceFile.record_request_event_uuid == request.event_uuid,
        StudyEvidenceFile.purpose == "record_request_evidence",
    ).order_by(StudyEvidenceFile.evidence_uuid).limit(501))).scalars().all()
    if len(files) > 500:
        raise studytomy_http_error(409, "RETENTION_DRY_RUN_TOO_LARGE", "The case requires separate retention review.")
    candidates = [
        _candidate(file, as_of, closed_at, configured, category, held=bool(active_holds))
        for file in files
    ]
    projection = {"request_reference": case_reference, "as_of": as_of, "hold_references": active_holds, "files": candidates}
    digest = canonical_request_hash({
        **projection, "files": [
            row.model_dump(mode="json") | {"content_sha256": file.content_sha256}
            for row, file in zip(candidates, files, strict=True)
        ],
        "policy_ids": sorted({policy.id for policy in policies} | set(authority_refs) | {market.id}),
        "hold_history": [event.event_uuid for event in [*holds, *releases]],
    })
    completed_removals = {}
    if files:
        expected = {file.evidence_uuid: file.content_sha256 for file in files}
        receipts = (await db.execute(select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.action == _REMOVED,
            StudyBusinessEvent.subject_uuid == request.subject_uuid,
            StudyBusinessEvent.resource_uuid.in_(list(expected)),
        ))).scalars().all()
        completed_removals = {
            receipt.resource_uuid: receipt.event_uuid for receipt in receipts
            if (receipt.after_state or {}).get("evidence_sha256") == expected[receipt.resource_uuid]
        }
    return EvidenceDryRun(**projection, snapshot_sha256=digest, completed_removals=completed_removals)


def _candidate(
    file: StudyEvidenceFile, as_of: datetime, closed_at: datetime | None,
    days: int, category: str, *, held: bool,
) -> EvidenceCandidate:
    state: Literal["held", "case_open", "unverified", "retained", "candidate"]
    eligible_at = None
    if held:
        state = "held"
    elif closed_at is None or closed_at > as_of:
        state = "case_open"
    elif file.upload_status != "ready" or file.verified_at is None or file.retention_category != category:
        state = "unverified"
    else:
        eligible_at = max(closed_at, file.verified_at) + timedelta(days=days)
        state = "candidate" if eligible_at <= as_of else "retained"
    return EvidenceCandidate(evidence_reference=file.evidence_uuid, disposition=state, eligible_at=eligible_at)


async def place_case_evidence_hold(
    db: AsyncSession, org: Organization, actor: PublicUser, case_reference: UUID,
    hold_reference: UUID, command: CaseEvidenceHoldCommand,
) -> CaseEvidenceHoldReceipt:
    org = await lock_organization(db, org.id)
    request, policies, authority_refs = await _staff_request_context(db, org, actor, case_reference)
    now = datetime.now(UTC)
    market, = await _current_policies(db, org, (StudyPolicyKey.D_18,), now)
    market_body, _ = inspect_study_policy_body(StudyPolicyKey.D_18, market.body)
    selected_market = market_body.specified_value("selected_market_requirements") if market_body else None
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_13, policies[2].body)
    retention = body.specified_value("retention") if body else None
    hold_rules = retention.get("case_evidence_holds") if isinstance(retention, dict) else None
    reasons = hold_rules.get("reason_codes") if isinstance(hold_rules, dict) else None
    days = hold_rules.get("max_review_days") if isinstance(hold_rules, dict) else None
    if (
        not isinstance(selected_market, dict) or selected_market.get("case_evidence_holds_enabled") is not True
        or not isinstance(reasons, list) or command.reason_code not in reasons
        or type(days) is not int or not 1 <= days <= 365
    ):
        raise studytomy_http_error(409, "RETENTION_HOLD_NOT_APPROVED", "Current policy does not approve this hold.")
    key = f"studytomy:retention:case_hold:{org.id}:{hold_reference}"
    review_due = command.review_due_at.astimezone(UTC)
    digest = canonical_request_hash({"reason_code": command.reason_code, "review_due_at": review_due.isoformat()})
    existing = (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id,
        StudyBusinessEvent.action == _HELD, StudyBusinessEvent.dedupe_key == key,
    ))).scalar_one_or_none()
    if existing is not None:
        if existing.operation_uuid != request.event_uuid or (existing.after_state or {}).get("source_snapshot_digest") != digest:
            raise studytomy_http_error(409, "RETENTION_HOLD_REFERENCE_CONFLICT", "The hold reference is already in use.")
        return CaseEvidenceHoldReceipt(
            hold_reference=hold_reference, review_due_at=datetime.fromisoformat(existing.after_state["review_due_at"]),
        )
    if not now < review_due <= now + timedelta(days=days):
        raise studytomy_http_error(409, "RETENTION_HOLD_REVIEW_DATE_INVALID", "The review date must be within the approved window.")
    await append_business_event(
        db, org_id=org.id, actor_user_id=actor.id, action=_HELD, reason_code="CASE_EVIDENCE_HOLD_RECORDED",
        subject_uuid=request.subject_uuid, resource_kind="record_request_hold", resource_uuid=str(hold_reference),
        operation_uuid=request.event_uuid, dedupe_key=key,
        policy_refs=sorted({policy.id for policy in policies} | set(authority_refs) | {market.id}),
        after_state={"status": "held", "decision_code": command.reason_code,
                     "review_due_at": review_due.isoformat(), "source_snapshot_digest": digest},
    )
    return CaseEvidenceHoldReceipt(hold_reference=hold_reference, review_due_at=review_due)


async def release_case_evidence_hold(
    db: AsyncSession, org: Organization, actor: PublicUser, case_reference: UUID,
    hold_reference: UUID, command: CaseEvidenceHoldReleaseCommand,
) -> CaseEvidenceHoldReleaseReceipt:
    org = await lock_organization(db, org.id)
    request, policies, authority_refs = await _staff_request_context(db, org, actor, case_reference)
    now = datetime.now(UTC)
    market, = await _current_policies(db, org, (StudyPolicyKey.D_18,), now)
    market_body, _ = inspect_study_policy_body(StudyPolicyKey.D_18, market.body)
    market_rules = market_body.specified_value("selected_market_requirements") if market_body else None
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_13, policies[2].body)
    retention = body.specified_value("retention") if body else None
    hold_rules = retention.get("case_evidence_holds") if isinstance(retention, dict) else None
    reasons = hold_rules.get("release_reason_codes") if isinstance(hold_rules, dict) else None
    if (
        not isinstance(market_rules, dict) or market_rules.get("case_evidence_hold_release_enabled") is not True
        or not isinstance(reasons, list) or command.reason_code not in reasons
    ):
        raise studytomy_http_error(409, "RETENTION_HOLD_RELEASE_NOT_APPROVED", "Current policy does not approve this hold release.")
    hold = (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.action == _HELD,
        StudyBusinessEvent.operation_uuid == request.event_uuid,
        StudyBusinessEvent.dedupe_key == f"studytomy:retention:case_hold:{org.id}:{hold_reference}",
    ))).scalar_one_or_none()
    if hold is None:
        raise studytomy_http_error(404, "RETENTION_HOLD_UNAVAILABLE", "Hold unavailable.")
    key = f"studytomy:retention:case_hold_release:{org.id}:{hold.event_uuid}"
    existing = (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.action == _RELEASED,
        StudyBusinessEvent.dedupe_key == key,
    ))).scalar_one_or_none()
    if existing is not None:
        if (existing.after_state or {}).get("decision_code") != command.reason_code:
            raise studytomy_http_error(409, "RETENTION_HOLD_ALREADY_RELEASED", "A different release is already retained.")
        return CaseEvidenceHoldReleaseReceipt(hold_reference=hold_reference, released_at=existing.created_at)
    event = await append_business_event(
        db, org_id=org.id, actor_user_id=actor.id, action=_RELEASED, reason_code="CASE_EVIDENCE_HOLD_RELEASED",
        subject_uuid=request.subject_uuid, resource_kind="record_request_hold", resource_uuid=str(hold_reference),
        operation_uuid=hold.event_uuid, dedupe_key=key,
        policy_refs=sorted({policy.id for policy in policies} | set(authority_refs) | {market.id}),
        after_state={"status": "released", "decision_code": command.reason_code},
    )
    return CaseEvidenceHoldReleaseReceipt(hold_reference=hold_reference, released_at=event.created_at)
