"""Transactional enqueueing and durable claiming for governed deliveries.

The outbox stores only an allowlisted template key and opaque resource
references. Rendering, recipient resolution and provider credentials stay in
the worker integration, and every delivery must be re-authorized there.
"""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.operations import (
    StudyOutboxPurpose,
    StudyOutboxRecord,
    StudyOutboxTemplate,
)

_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_SAFE_PROVIDER_REFERENCE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")


_TEMPLATE_BY_PURPOSE = {
    StudyOutboxPurpose.REQUIRED_NOTICE: StudyOutboxTemplate.REQUIRED_NOTICE,
    StudyOutboxPurpose.RECEIPT: StudyOutboxTemplate.RECEIPT,
    StudyOutboxPurpose.PROGRESS_REPORT: StudyOutboxTemplate.PROGRESS_REPORT,
    StudyOutboxPurpose.OPTIONAL_REMINDER: StudyOutboxTemplate.OPTIONAL_REMINDER,
    StudyOutboxPurpose.MARKETING: StudyOutboxTemplate.MARKETING,
}


def _opaque_key_hash(value: str, *, label: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 255
        or any(ord(char) < 33 or ord(char) > 126 for char in normalized)
    ):
        raise HTTPException(status_code=422, detail=f"{label} must be 1 to 255 visible ASCII characters")
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()


def _payload_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    """Normalize database timestamps before comparing lease boundaries.

    PostgreSQL preserves the timezone on ``timestamptz`` values. SQLite drops
    it when reading ``DateTime(timezone=True)``, so tests and local tooling can
    return a naive value for the same UTC instant.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


async def enqueue_delivery(
    db_session: AsyncSession,
    *,
    org_id: int,
    recipient_user_id: int,
    purpose: StudyOutboxPurpose,
    resource_kind: str,
    resource_uuid: str,
    dedupe_key: str,
    policy_refs: Iterable[int],
    created_by_user_id: int | None = None,
    resource_version: int | None = None,
    authority_reference_uuid: str | None = None,
    available_at: datetime | None = None,
) -> tuple[StudyOutboxRecord, bool]:
    """Add delivery intent to the caller's transaction, or return its replay.

    The boolean is true when the dedupe key already names the same request.
    No message body, recipient address, consent text or raw dedupe key is saved.
    """
    try:
        purpose = StudyOutboxPurpose(purpose)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Unsupported delivery purpose") from error
    if (
        not resource_kind
        or len(resource_kind) > 32
        or not resource_uuid
        or len(resource_uuid) > 100
        or any(ord(char) < 33 for char in resource_kind + resource_uuid)
    ):
        raise HTTPException(status_code=422, detail="A bounded resource reference is required")
    if resource_version is not None and resource_version < 1:
        raise HTTPException(status_code=422, detail="resource_version must be positive")
    if purpose in {
        StudyOutboxPurpose.PROGRESS_REPORT,
        StudyOutboxPurpose.OPTIONAL_REMINDER,
        StudyOutboxPurpose.MARKETING,
    } and not authority_reference_uuid:
        raise HTTPException(
            status_code=409,
            detail="An approved recipient authority or consent reference is required",
        )

    raw_policy_refs = list(policy_refs)
    if any(type(ref) is not int or ref < 1 for ref in raw_policy_refs):
        raise HTTPException(status_code=409, detail="Approved policy references are invalid")
    normalized_policy_refs = sorted(set(raw_policy_refs))
    if not normalized_policy_refs:
        raise HTTPException(status_code=409, detail="Approved policy references are required for delivery")
    if authority_reference_uuid is not None and (
        not authority_reference_uuid.strip()
        or len(authority_reference_uuid) > 100
        or any(ord(char) < 33 for char in authority_reference_uuid)
    ):
        raise HTTPException(status_code=422, detail="authority_reference_uuid must be a bounded opaque reference")
    due_at = available_at or datetime.now(timezone.utc)
    if due_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="available_at must include a timezone")
    due_at = due_at.astimezone(timezone.utc)

    organization_result = await db_session.execute(
        select(Organization).where(Organization.id == org_id).with_for_update()
    )
    if organization_result.scalars().first() is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    dedupe_hash = _opaque_key_hash(dedupe_key, label="Delivery dedupe key")
    template = _TEMPLATE_BY_PURPOSE[purpose]
    request_hash = _payload_hash(
        {
            "recipient_user_id": recipient_user_id,
            "purpose": purpose.value,
            "template_key": template.value,
            "resource_kind": resource_kind,
            "resource_uuid": resource_uuid,
            "resource_version": resource_version,
            "authority_reference_uuid": authority_reference_uuid,
            "policy_refs": normalized_policy_refs,
            "available_at": available_at.astimezone(timezone.utc).isoformat() if available_at else None,
        }
    )
    existing_result = await db_session.execute(
        select(StudyOutboxRecord)
        .where(
            StudyOutboxRecord.org_id == org_id,
            StudyOutboxRecord.dedupe_hash == dedupe_hash,
        )
        .with_for_update()
    )
    existing = existing_result.scalars().first()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "DELIVERY_DEDUPE_KEY_REUSED",
                    "message": "This delivery key was already used for a different delivery.",
                },
            )
        return existing, True

    now = datetime.now(timezone.utc)
    record = StudyOutboxRecord(
        org_id=org_id,
        recipient_user_id=recipient_user_id,
        purpose=purpose.value,
        template_key=template.value,
        resource_kind=resource_kind,
        resource_uuid=resource_uuid,
        resource_version=resource_version,
        authority_reference_uuid=authority_reference_uuid,
        policy_refs=normalized_policy_refs,
        dedupe_hash=dedupe_hash,
        request_hash=request_hash,
        status="queued",
        attempt_count=0,
        next_attempt_at=due_at,
        created_by_user_id=created_by_user_id,
        created_at=now,
        updated_at=now,
    )
    db_session.add(record)
    await db_session.flush()
    return record, False


async def claim_due_deliveries(
    db_session: AsyncSession,
    *,
    worker_id: str,
    limit: int = 25,
    now: datetime | None = None,
    lease_seconds: int = 60,
    purpose: StudyOutboxPurpose | None = None,
) -> list[StudyOutboxRecord]:
    """Lease a bounded batch; expired sends require reconciliation, never replay."""
    if not worker_id or len(worker_id) > 100 or any(ord(char) < 33 or ord(char) > 126 for char in worker_id):
        raise HTTPException(status_code=422, detail="worker_id must be bounded visible ASCII")
    if not 1 <= limit <= 100 or not 1 <= lease_seconds <= 900:
        raise HTTPException(status_code=422, detail="Invalid outbox claim bounds")

    claimed_at = _as_utc(now or datetime.now(timezone.utc))
    purpose_filter = (StudyOutboxRecord.purpose == purpose.value,) if purpose else ()
    expired_result = await db_session.execute(
        select(StudyOutboxRecord)
        .where(
            *purpose_filter,
            StudyOutboxRecord.status == "leased",
            StudyOutboxRecord.lease_expires_at <= claimed_at,
        )
        .order_by(StudyOutboxRecord.lease_expires_at, StudyOutboxRecord.id)
        .with_for_update(skip_locked=True)
        .limit(limit)
        .execution_options(populate_existing=True)
    )
    for expired in expired_result.scalars().all():
        expired.status = "reconciliation_required"
        expired.version += 1
        expired.lease_owner = None
        expired.lease_expires_at = None
        expired.last_error_code = "LEASE_EXPIRED_OUTCOME_UNKNOWN"
        expired.completed_at = None
        expired.updated_at = claimed_at

    due_result = await db_session.execute(
        select(StudyOutboxRecord)
        .where(
            *purpose_filter,
            StudyOutboxRecord.status.in_(("queued", "retry")),
            StudyOutboxRecord.next_attempt_at <= claimed_at,
        )
        .order_by(StudyOutboxRecord.next_attempt_at, StudyOutboxRecord.created_at)
        .with_for_update(skip_locked=True)
        .limit(limit)
        .execution_options(populate_existing=True)
    )
    records = list(due_result.scalars().all())
    expiry = claimed_at + timedelta(seconds=lease_seconds)
    for record in records:
        record.status = "leased"
        record.version += 1
        record.attempt_count += 1
        record.lease_owner = worker_id
        record.lease_expires_at = expiry
        record.last_error_code = None
        record.updated_at = claimed_at
    await db_session.flush()
    return records


async def release_delivery_lease(
    db_session: AsyncSession,
    record: StudyOutboxRecord,
    *,
    worker_id: str,
    status_value: str,
    now: datetime | None = None,
    error_code: str | None = None,
    provider_message_reference: str | None = None,
    retry_at: datetime | None = None,
) -> None:
    """Transition a delivery only while the same worker still owns its lease."""
    locked_result = await db_session.execute(
        select(StudyOutboxRecord)
        .where(StudyOutboxRecord.id == record.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    locked = locked_result.scalars().first()
    current = _as_utc(now or datetime.now(timezone.utc))
    if (
        locked is None
        or locked.status != "leased"
        or locked.lease_owner != worker_id
        or locked.lease_expires_at is None
        or _as_utc(locked.lease_expires_at) <= current
    ):
        raise HTTPException(status_code=409, detail="The delivery lease is no longer owned by this worker")
    if status_value not in {"retry", "sent", "suppressed", "reconciliation_required", "dead_letter"}:
        raise HTTPException(status_code=422, detail="Invalid delivery outcome")
    if status_value == "retry" and retry_at is None:
        raise HTTPException(status_code=422, detail="A retry time is required")
    if error_code is not None and not _SAFE_CODE.fullmatch(error_code):
        raise HTTPException(status_code=422, detail="error_code must be a stable safe code")
    if provider_message_reference is not None and not _SAFE_PROVIDER_REFERENCE.fullmatch(provider_message_reference):
        raise HTTPException(status_code=422, detail="provider reference has an invalid format")
    if retry_at is not None and retry_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="retry_at must include a timezone")
    if retry_at is not None:
        retry_at = _as_utc(retry_at)

    locked.status = status_value
    locked.version += 1
    locked.lease_owner = None
    locked.lease_expires_at = None
    locked.last_error_code = error_code
    locked.provider_message_reference = provider_message_reference
    locked.next_attempt_at = retry_at or locked.next_attempt_at
    locked.completed_at = current if status_value in {"sent", "suppressed", "dead_letter"} else None
    locked.updated_at = current
    await db_session.flush()
