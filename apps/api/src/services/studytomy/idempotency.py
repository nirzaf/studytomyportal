"""Replay protection for Studytomy writes (TASK-005, BR-PAY-04/05, BR-SUB-07).

The database is the authority: a unique ``(org, actor, operation, key_hash)`` row
is what makes a duplicate submit safe, never a cache lock. A claim only flushes;
the enclosing use case commits, so a rolled-back mutation also rolls back its
replay guard and the request can legitimately be tried again.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.studytomy.operations import (
    StudyIdempotencyRecord,
    StudyIdempotencyStatus,
)
from src.services.studytomy.errors import (
    IDEMPOTENCY_CONFLICT,
    studytomy_http_error,
)

# Engineering bounds, not service promises: a live claim blocks a second attempt
# for this long, and a finished guard is retained for replay this long. Both stay
# well inside any window a client realistically uses. The approved retention
# period remains a D-15 input, so nothing here claims a compliance number.
LEASE_SECONDS = 300
RETENTION_DAYS = 30


class IdempotencyReplay(BaseModel):
    """A committed result safe to return again for the same key."""

    model_config = ConfigDict(extra="forbid")

    resource_kind: Optional[str]
    resource_uuid: Optional[str]
    response_status: Optional[int]
    response_projection: Optional[dict]


def canonical_request_hash(payload: Any) -> str:
    """Hash the request semantically, so key order and formatting never matter."""
    if isinstance(payload, BaseModel):
        data = payload.model_dump(mode="json")
    else:
        data = payload
    encoded = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _key_hash(idempotency_key: str) -> str:
    """Normalize the client key and hash it; the key itself is never stored."""
    normalized = idempotency_key.strip()
    if (
        not normalized
        or len(normalized) > 255
        or any(ord(char) < 33 or ord(char) > 126 for char in normalized)
    ):
        raise studytomy_http_error(
            422,
            IDEMPOTENCY_CONFLICT,
            "Idempotency-Key must be 1 to 255 visible ASCII characters.",
            field_errors={"Idempotency-Key": "Invalid format"},
        )
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _find(
    db_session: AsyncSession,
    org_id: int,
    actor_user_id: int,
    operation: str,
    key_hash: str,
) -> Optional[StudyIdempotencyRecord]:
    result = await db_session.execute(
        select(StudyIdempotencyRecord).where(
            StudyIdempotencyRecord.org_id == org_id,
            StudyIdempotencyRecord.actor_user_id == actor_user_id,
            StudyIdempotencyRecord.operation == operation,
            StudyIdempotencyRecord.key_hash == key_hash,
        ).with_for_update()
    )
    return result.scalars().first()


async def claim_idempotency(
    db_session: AsyncSession,
    *,
    org_id: int,
    actor_user_id: int,
    operation: str,
    idempotency_key: Optional[str],
    request_payload: Any,
) -> Optional[IdempotencyReplay]:
    """Take ownership of one key, or return the committed result to replay.

    Returns ``None`` when the caller must perform the mutation. Raises
    ``409 IDEMPOTENCY_CONFLICT`` when the same key is reused for a different
    request, or while another attempt still holds an unexpired lease. A claim
    left by a crashed request expires, so the key is never permanently stranded.
    """
    if idempotency_key is None:
        return None

    hashed_key = _key_hash(idempotency_key)
    request_hash = canonical_request_hash(request_payload)
    now = datetime.now(timezone.utc)

    record = await _find(db_session, org_id, actor_user_id, operation, hashed_key)
    if record is None:
        candidate = StudyIdempotencyRecord(
            org_id=org_id,
            actor_user_id=actor_user_id,
            operation=operation,
            key_hash=hashed_key,
            request_hash=request_hash,
            status=StudyIdempotencyStatus.PROCESSING,
            expires_at=now + timedelta(seconds=LEASE_SECONDS),
            created_at=now,
        )
        try:
            # A savepoint keeps a lost race from poisoning the caller's own
            # transaction, which still has to report the replayed result.
            async with db_session.begin_nested():
                db_session.add(candidate)
                await db_session.flush()
        except IntegrityError:
            record = await _find(db_session, org_id, actor_user_id, operation, hashed_key)
            if record is None:
                raise studytomy_http_error(
                    409,
                    IDEMPOTENCY_CONFLICT,
                    "This idempotency key is being claimed by another request.",
                    next_action="Retry with the same request body to receive the original result.",
                ) from None
        else:
            return None

    if record.request_hash != request_hash:
        raise studytomy_http_error(
            409,
            IDEMPOTENCY_CONFLICT,
            "This idempotency key was already used for a different request.",
            next_action="Send the original body again, or use a new idempotency key.",
        )

    if record.status == StudyIdempotencyStatus.COMPLETED:
        return IdempotencyReplay(
            resource_kind=record.resource_kind,
            resource_uuid=record.resource_uuid,
            response_status=record.response_status,
            response_projection=record.response_projection,
        )

    expires_at = record.expires_at
    if expires_at is not None and _as_utc(expires_at) > now:
        raise studytomy_http_error(
            409,
            IDEMPOTENCY_CONFLICT,
            "The original request with this idempotency key is still running.",
            next_action="Retry shortly; the result is replayed once the first attempt finishes.",
        )

    # The previous attempt died without committing. Take the lease over rather
    # than strand the key: no mutation was recorded, so retrying is safe.
    record.expires_at = now + timedelta(seconds=LEASE_SECONDS)
    db_session.add(record)
    await db_session.flush()
    return None


async def complete_idempotency(
    db_session: AsyncSession,
    *,
    org_id: int,
    actor_user_id: int,
    operation: str,
    idempotency_key: Optional[str],
    resource_kind: str,
    resource_uuid: str,
    response_status: Optional[int] = None,
    response_projection: Optional[dict] = None,
) -> None:
    """Publish the result so a retry replays it instead of repeating the write."""
    if idempotency_key is None:
        return
    record = await _find(
        db_session,
        org_id,
        actor_user_id,
        operation,
        _key_hash(idempotency_key),
    )
    if record is None:
        raise studytomy_http_error(
            409,
            IDEMPOTENCY_CONFLICT,
            "The idempotency claim for this request is unavailable.",
        )
    now = datetime.now(timezone.utc)
    record.status = StudyIdempotencyStatus.COMPLETED
    record.resource_kind = resource_kind
    record.resource_uuid = resource_uuid
    record.response_status = response_status
    record.response_projection = response_projection
    record.completed_at = now
    # The same column changes meaning once the result exists: it becomes the
    # horizon until which a replay may be served, and after which the resource's
    # own uniqueness constraints stand alone.
    record.expires_at = now + timedelta(days=RETENTION_DAYS)
    db_session.add(record)
    await db_session.flush()


async def release_idempotency_lease(
    db_session: AsyncSession,
    *,
    org_id: int,
    actor_user_id: int,
    operation: str,
    idempotency_key: Optional[str],
) -> None:
    """Expire a live claim so the same key may legitimately be retried.

    Only for a use case that handled its own failure without rolling back the
    transaction; a mutation that raised all the way out loses its claim with the
    rollback, which is the normal path.
    """
    if idempotency_key is None:
        return
    record = await _find(
        db_session,
        org_id,
        actor_user_id,
        operation,
        _key_hash(idempotency_key),
    )
    if record is None or record.status == StudyIdempotencyStatus.COMPLETED:
        return
    record.expires_at = datetime.now(timezone.utc)
    db_session.add(record)
    await db_session.flush()
