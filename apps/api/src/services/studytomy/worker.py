"""Fail-closed delivery worker primitives for the same application runtime.

The optional in-process scheduler is default-off. Decision gates D-04, D-12,
D-13, D-14 and D-18 remain unapproved in this checkout; no schedule is enabled
and dispatch still revalidates recipient, report and policy authority before any provider call.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol

from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.studytomy.operations import StudyOutboxPurpose, StudyOutboxRecord
from src.services.studytomy.outbox import (
    _as_utc,
    claim_due_deliveries,
    release_delivery_lease,
)
from src.services.studytomy.policies import lock_organization

_SAFE_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_SAFE_PROVIDER_REFERENCE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
_MAX_ATTEMPTS = 8


@dataclass(frozen=True)
class DeliveryAuthorization:
    """A fresh policy result assembled from authoritative rows at dispatch time."""

    purpose_allowed: bool
    consent_current: bool
    relationship_current: bool
    resource_complete: bool
    resource_version: int | None
    denial_code: str | None = None

    @property
    def allowed(self) -> bool:
        return (
            self.purpose_allowed
            and self.consent_current
            and self.relationship_current
            and self.resource_complete
        )


@dataclass(frozen=True)
class ProviderReceipt:
    accepted: bool
    message_reference: str | None = None


class ProviderTransientFailure(Exception):
    """The provider confirmed it did not accept the message and retry is safe."""


class ProviderOutcomeUnknown(Exception):
    """The provider may have accepted the message; automatic resend is unsafe."""


class RecipientRevalidationFailure(Exception):
    """Current consent, relationship or purpose could not be verified."""


class RevalidateDelivery(Protocol):
    async def __call__(
        self,
        db_session: AsyncSession,
        record: StudyOutboxRecord,
    ) -> DeliveryAuthorization: ...


class SendDelivery(Protocol):
    async def __call__(
        self,
        db_session: AsyncSession,
        record: StudyOutboxRecord,
        authorization: DeliveryAuthorization,
        provider_idempotency_key: str,
    ) -> ProviderReceipt: ...


async def run_delivery_batch(
    db_session: AsyncSession,
    *,
    worker_id: str,
    revalidate: RevalidateDelivery,
    send: SendDelivery,
    limit: int = 25,
    now: datetime | None = None,
    purpose: StudyOutboxPurpose | None = None,
) -> dict[str, int]:
    """Claim and process a bounded batch with recipient/resource revalidation.

    The revalidation callback must lock the relationship, consent and source
    rows it relies on and must not commit. The send callback renders only from
    current authorized source data; it must never trust a saved message body.
    Provider errors are persisted as stable codes only, without response text.
    """
    started_at = _as_utc(now or datetime.now(timezone.utc))
    claimed = await claim_due_deliveries(
        db_session,
        worker_id=worker_id,
        limit=limit,
        now=started_at,
        purpose=purpose,
    )
    claimed_ids = [(record.id, record.org_id) for record in claimed]
    await db_session.commit()
    outcomes = {
        "claimed": len(claimed_ids),
        "sent": 0,
        "suppressed": 0,
        "retry": 0,
        "reconciliation_required": 0,
        "dead_letter": 0,
    }

    for record_id, org_id in claimed_ids:
        await lock_organization(db_session, org_id)
        locked_result = await db_session.execute(
            select(StudyOutboxRecord)
            .where(StudyOutboxRecord.id == record_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        record = locked_result.scalars().first()
        current_time = _as_utc(datetime.now(timezone.utc))
        if (
            record is None
            or record.status != "leased"
            or record.lease_owner != worker_id
            or record.lease_expires_at is None
            or _as_utc(record.lease_expires_at) <= current_time
        ):
            await db_session.rollback()
            continue

        try:
            authorization = await revalidate(db_session, record)
        except RecipientRevalidationFailure:
            await _retry_or_dead_letter(
                db_session,
                record,
                worker_id,
                current_time,
                "RECIPIENT_REVALIDATION_UNAVAILABLE",
                outcomes,
            )
            await db_session.commit()
            continue
        except Exception:
            # Unknown authorization state never permits a send. Keep only a
            # safe code, not provider or exception content that may include PII.
            await _retry_or_dead_letter(
                db_session,
                record,
                worker_id,
                current_time,
                "RECIPIENT_REVALIDATION_FAILURE",
                outcomes,
            )
            await db_session.commit()
            continue

        if not authorization.allowed:
            denial_code = authorization.denial_code or "RECIPIENT_AUTHORIZATION_DENIED"
            if not _SAFE_CODE.fullmatch(denial_code):
                denial_code = "RECIPIENT_AUTHORIZATION_DENIED"
            await release_delivery_lease(
                db_session,
                record,
                worker_id=worker_id,
                status_value="suppressed",
                now=current_time,
                error_code=denial_code,
            )
            outcomes["suppressed"] += 1
            await db_session.commit()
            continue

        if (
            record.resource_version is not None
            and authorization.resource_version != record.resource_version
        ):
            await release_delivery_lease(
                db_session,
                record,
                worker_id=worker_id,
                status_value="suppressed",
                now=current_time,
                error_code="RESOURCE_VERSION_CHANGED",
            )
            outcomes["suppressed"] += 1
            await db_session.commit()
            continue

        try:
            receipt = await send(
                db_session,
                record,
                authorization,
                record.provider_idempotency_key,
            )
        except ProviderTransientFailure:
            await _retry_or_dead_letter(
                db_session,
                record,
                worker_id,
                current_time,
                "PROVIDER_TEMPORARILY_UNAVAILABLE",
                outcomes,
            )
        except ProviderOutcomeUnknown:
            await release_delivery_lease(
                db_session,
                record,
                worker_id=worker_id,
                status_value="reconciliation_required",
                now=current_time,
                error_code="PROVIDER_OUTCOME_UNKNOWN",
            )
            outcomes["reconciliation_required"] += 1
        except Exception:
            await release_delivery_lease(
                db_session,
                record,
                worker_id=worker_id,
                status_value="reconciliation_required",
                now=current_time,
                error_code="PROVIDER_OUTCOME_UNKNOWN",
            )
            outcomes["reconciliation_required"] += 1
        else:
            reference = receipt.message_reference if isinstance(receipt, ProviderReceipt) else None
            if (
                not isinstance(receipt, ProviderReceipt)
                or receipt.accepted is not True
                or (
                    reference is not None
                    and (
                        not isinstance(reference, str)
                        or not _SAFE_PROVIDER_REFERENCE.fullmatch(reference)
                    )
                )
            ):
                await release_delivery_lease(
                    db_session,
                    record,
                    worker_id=worker_id,
                    status_value="reconciliation_required",
                    now=current_time,
                    error_code="PROVIDER_OUTCOME_UNKNOWN",
                )
                outcomes["reconciliation_required"] += 1
            else:
                await release_delivery_lease(
                    db_session,
                    record,
                    worker_id=worker_id,
                    status_value="sent",
                    now=current_time,
                    provider_message_reference=reference,
                )
                outcomes["sent"] += 1
        await db_session.commit()

    return outcomes


async def _retry_or_dead_letter(
    db_session: AsyncSession,
    record: StudyOutboxRecord,
    worker_id: str,
    now: datetime,
    error_code: str,
    outcomes: dict[str, int],
) -> None:
    if record.attempt_count >= _MAX_ATTEMPTS:
        await release_delivery_lease(
            db_session,
            record,
            worker_id=worker_id,
            status_value="dead_letter",
            now=now,
            error_code=error_code,
        )
        outcomes["dead_letter"] += 1
        return
    delay_seconds = min(15 * (2 ** max(record.attempt_count - 1, 0)), 3600)
    await release_delivery_lease(
        db_session,
        record,
        worker_id=worker_id,
        status_value="retry",
        now=now,
        error_code=error_code,
        retry_at=now + timedelta(seconds=delay_seconds),
    )
    outcomes["retry"] += 1
