"""Execute one approved case-evidence file removal at a time."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel
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
from src.services.studytomy.evidence_files import (
    _SUPPORTED_MEDIA,
    _verify_private_evidence,
)
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.record_requests import _staff_request_context
from src.services.studytomy.retention import (
    _REMOVAL_APPROVED,
    _REMOVED,
    dry_run_case_evidence_retention,
)
from src.services.utils.upload_content import (
    _CONTENT_ROOT,
    _safe_content_path,
    get_learnhouse_config,
)

_INTENT = "studytomy.retention.case_evidence_removal_started"
_REPLAYED = "studytomy.retention.case_evidence_restore_replayed"


class EvidenceRemovalReceipt(BaseModel):
    evidence_reference: str
    removal_receipt: UUID
    removed_at: datetime


async def _context(db, org, actor, case_reference, approval_reference, evidence_reference, *, restore=False):
    request, _, _ = await _staff_request_context(db, org, actor, case_reference)
    approval = (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.action == _REMOVAL_APPROVED,
        StudyBusinessEvent.operation_uuid == request.event_uuid,
        StudyBusinessEvent.resource_uuid == str(approval_reference),
    ))).scalar_one_or_none()
    if approval is None:
        raise studytomy_http_error(404, "RETENTION_APPROVAL_UNAVAILABLE", "Removal approval unavailable.")
    state = approval.after_state or {}
    manifest = await dry_run_case_evidence_retention(
        db, org, actor, case_reference, datetime.fromisoformat(state["retention_cutoff"]),
    )
    if manifest.snapshot_sha256 != state.get("source_snapshot_digest"):
        raise studytomy_http_error(409, "RETENTION_MANIFEST_CHANGED", "A new removal review is required.")
    if not any(file.evidence_reference == evidence_reference and file.disposition == "candidate" for file in manifest.files):
        raise studytomy_http_error(409, "RETENTION_FILE_NOT_ELIGIBLE", "The file is unavailable for removal.")
    retention, market = await _current_policies(
        db, org, (StudyPolicyKey.D_13, StudyPolicyKey.D_18), datetime.now(UTC),
    )
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_18, market.body)
    market_rules = body.specified_value("selected_market_requirements") if body else None
    policy_ids = sorted([retention.id, market.id])
    if (
        not isinstance(market_rules, dict) or market_rules.get("case_evidence_local_execution_enabled") is not True
        or approval.policy_refs != policy_ids
        or get_learnhouse_config().hosting_config.content_delivery.type != "filesystem"
        or (restore and market_rules.get("case_evidence_local_restore_replay_enabled") is not True)
    ):
        raise studytomy_http_error(409, "RETENTION_EXECUTION_NOT_APPROVED", "Current policy and storage do not permit execution.")
    file = (await db.execute(select(StudyEvidenceFile).where(
        StudyEvidenceFile.org_id == org.id, StudyEvidenceFile.record_request_event_uuid == request.event_uuid,
        StudyEvidenceFile.purpose == "record_request_evidence", StudyEvidenceFile.evidence_uuid == evidence_reference,
    ).with_for_update().execution_options(populate_existing=True))).scalar_one()
    return request, approval, file


async def _stored_event(db, org_id, action, key):
    return (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org_id, StudyBusinessEvent.action == action, StudyBusinessEvent.dedupe_key == key,
    ))).scalar_one_or_none()


def _path(org: Organization, file: StudyEvidenceFile) -> Path:
    format_info = _SUPPORTED_MEDIA.get(file.media_type)
    if format_info is None:
        raise studytomy_http_error(409, "RETENTION_STORAGE_REFERENCE_INVALID", "Private storage reference is invalid.")
    filename = f"{file.evidence_uuid}{format_info[1]}"
    if file.storage_reference != f"content/orgs/{org.org_uuid}/studytomy/evidence/{filename}":
        raise studytomy_http_error(409, "RETENTION_STORAGE_REFERENCE_INVALID", "Private storage reference is invalid.")
    expected = Path(_CONTENT_ROOT).resolve() / "orgs" / org.org_uuid / "studytomy" / "evidence" / filename
    if Path(_safe_content_path("orgs", org.org_uuid, "studytomy/evidence", filename)) != expected:
        raise studytomy_http_error(409, "RETENTION_STORAGE_REFERENCE_INVALID", "Private storage reference is invalid.")
    return expected


async def remove_approved_case_evidence(
    db: AsyncSession, org: Organization, actor: PublicUser, case_reference: UUID,
    approval_reference: UUID, evidence_reference: str,
) -> EvidenceRemovalReceipt:
    request, approval, file = await _context(db, org, actor, case_reference, approval_reference, evidence_reference)
    intent_key = f"studytomy:retention:case_removal_intent:{org.id}:{approval.event_uuid}:{evidence_reference}"
    receipt_key = f"studytomy:retention:case_removal_done:{org.id}:{evidence_reference}"
    completed = await _stored_event(db, org.id, _REMOVED, receipt_key)
    if completed:
        return _receipt(completed)
    intent = await _stored_event(db, org.id, _INTENT, intent_key)
    if intent is None:
        _path(org, file)
        await _verify_private_evidence(org, file)
        await append_business_event(
            db, org_id=org.id, actor_user_id=actor.id, action=_INTENT, reason_code="APPROVED_CASE_EVIDENCE_REMOVAL_STARTED",
            subject_uuid=request.subject_uuid, resource_kind="record_request_evidence", resource_uuid=evidence_reference,
            operation_uuid=approval.event_uuid, dedupe_key=intent_key, policy_refs=approval.policy_refs,
            after_state={"evidence_sha256": file.content_sha256},
        )
        await db.commit()
        request, approval, file = await _context(db, org, actor, case_reference, approval_reference, evidence_reference)
        completed = await _stored_event(db, org.id, _REMOVED, receipt_key)
        if completed:
            return _receipt(completed)
    await _remove_verified_file(org, file)
    event = await append_business_event(
        db, org_id=org.id, actor_user_id=actor.id, action=_REMOVED, reason_code="APPROVED_CASE_EVIDENCE_REMOVED",
        subject_uuid=request.subject_uuid, resource_kind="record_request_evidence", resource_uuid=evidence_reference,
        operation_uuid=approval.event_uuid, dedupe_key=receipt_key, policy_refs=approval.policy_refs,
        after_state={"evidence_sha256": file.content_sha256},
    )
    return _receipt(event)


async def _remove_verified_file(org: Organization, file: StudyEvidenceFile) -> None:
    path = _path(org, file)
    try:
        await _verify_private_evidence(org, file)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise studytomy_http_error(503, "RETENTION_STORAGE_UNAVAILABLE", "Private evidence removal could not be confirmed.") from exc


async def replay_case_evidence_removal(
    db: AsyncSession, org: Organization, actor: PublicUser, case_reference: UUID,
    approval_reference: UUID, evidence_reference: str, replay_reference: UUID,
) -> EvidenceRemovalReceipt:
    request, approval, file = await _context(
        db, org, actor, case_reference, approval_reference, evidence_reference, restore=True,
    )
    removed_key = f"studytomy:retention:case_removal_done:{org.id}:{evidence_reference}"
    removed = await _stored_event(db, org.id, _REMOVED, removed_key)
    if (
        removed is None or removed.operation_uuid != approval.event_uuid
        or (removed.after_state or {}).get("evidence_sha256") != file.content_sha256
    ):
        raise studytomy_http_error(409, "RETENTION_TOMBSTONE_UNAVAILABLE", "An exact retained removal receipt is required.")
    key = f"studytomy:retention:restore_replay:{org.id}:{replay_reference}"
    previous = await _stored_event(db, org.id, _REPLAYED, key)
    if previous is not None:
        if previous.operation_uuid != removed.event_uuid:
            raise studytomy_http_error(409, "RETENTION_REPLAY_REFERENCE_CONFLICT", "The replay reference is already in use.")
        _path(org, file)
        try:
            await _verify_private_evidence(org, file)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            return _receipt(previous)
        raise studytomy_http_error(409, "RETENTION_NEW_REPLAY_REQUIRED", "Use a new reference for another restored copy.")
    await _remove_verified_file(org, file)
    event = await append_business_event(
        db, org_id=org.id, actor_user_id=actor.id, action=_REPLAYED, reason_code="CASE_EVIDENCE_RESTORE_REPLAYED",
        subject_uuid=request.subject_uuid, resource_kind="record_request_evidence", resource_uuid=evidence_reference,
        operation_uuid=removed.event_uuid, dedupe_key=key, policy_refs=approval.policy_refs,
        after_state={"evidence_sha256": file.content_sha256},
    )
    return _receipt(event)


def _receipt(event: StudyBusinessEvent) -> EvidenceRemovalReceipt:
    return EvidenceRemovalReceipt(
        evidence_reference=event.resource_uuid, removal_receipt=event.event_uuid, removed_at=event.created_at,
    )
