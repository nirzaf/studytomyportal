"""Default-off lifecycle poller for the existing Studytomy report outbox."""

import asyncio
import logging
import os
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.studytomy.operations import StudyOutboxPurpose, StudyOutboxRecord

logger = logging.getLogger(__name__)

_ENABLE_ENV = "LEARNHOUSE_STUDYTOMY_REPORT_DELIVERY_ENABLED"
_POLL_INTERVAL_SECONDS = 60
_PENDING_STATUSES = frozenset({"queued", "leased", "retry"})
_SAFE_STATUSES = frozenset(
    {
        "queued",
        "leased",
        "retry",
        "sent",
        "suppressed",
        "reconciliation_required",
        "dead_letter",
    }
)
_SAFE_FAILURE_CODES = frozenset(
    {
        "LEASE_EXPIRED_OUTCOME_UNKNOWN",
        "RECIPIENT_AUTHORIZATION_DENIED",
        "RECIPIENT_REVALIDATION_UNAVAILABLE",
        "RECIPIENT_REVALIDATION_FAILURE",
        "RESOURCE_VERSION_CHANGED",
        "PROVIDER_TEMPORARILY_UNAVAILABLE",
        "PROVIDER_OUTCOME_UNKNOWN",
        "REPORT_RECIPIENT_NOT_AUTHORIZED",
    }
)
_LOG_STATUS_LABELS = {
    "sent": "provider_accepted",
    "reconciliation_required": "provider_outcome_unknown",
}
_task: asyncio.Task | None = None
_worker_id: str | None = None


async def _queue_summary(db_session: AsyncSession) -> dict:
    rows = (
        await db_session.execute(
            select(
                StudyOutboxRecord.status,
                StudyOutboxRecord.last_error_code,
                func.count(),
                func.min(StudyOutboxRecord.created_at),
            )
            .where(StudyOutboxRecord.purpose == StudyOutboxPurpose.PROGRESS_REPORT.value)
            .group_by(StudyOutboxRecord.status, StudyOutboxRecord.last_error_code)
        )
    ).all()
    now = datetime.now(UTC)
    status_counts: dict[str, int] = {}
    failure_code_counts: dict[str, int] = {}
    oldest_pending_age_seconds = 0
    for raw_status, error_code, count, oldest_created_at in rows:
        status = raw_status if raw_status in _SAFE_STATUSES else "unknown"
        log_status = _LOG_STATUS_LABELS.get(status, status)
        status_counts[log_status] = status_counts.get(log_status, 0) + count
        if status in _PENDING_STATUSES and oldest_created_at is not None:
            created_at = oldest_created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            else:
                created_at = created_at.astimezone(UTC)
            oldest_pending_age_seconds = max(
                oldest_pending_age_seconds,
                max(0, int((now - created_at).total_seconds())),
            )
        if error_code in _SAFE_FAILURE_CODES:
            failure_code_counts[error_code] = failure_code_counts.get(error_code, 0) + count
    return {
        "status_counts": status_counts,
        "oldest_pending_age_seconds": oldest_pending_age_seconds,
        "failure_code_counts": failure_code_counts,
    }


async def _run_once() -> None:
    from src.core.events.database import _async_session_factory
    from src.services.studytomy.report_notifications import run_report_delivery_batch

    worker_id = _worker_id
    if worker_id is None:
        return
    async with _async_session_factory() as db_session:
        tally = await run_report_delivery_batch(db_session, worker_id=worker_id)
        summary = await _queue_summary(db_session)

    if tally["claimed"] or summary["failure_code_counts"] or any(
        summary["status_counts"].get(status, 0) for status in _PENDING_STATUSES
    ):
        logger.info(
            "Studytomy report delivery batch: claimed=%d provider_accepted=%d suppressed=%d "
            "retry=%d reconciliation_required=%d dead_letter=%d status_counts=%s "
            "oldest_pending_age_seconds=%d failure_code_counts=%s",
            tally["claimed"],
            tally["sent"],
            tally["suppressed"],
            tally["retry"],
            tally["reconciliation_required"],
            tally["dead_letter"],
            summary["status_counts"],
            summary["oldest_pending_age_seconds"],
            summary["failure_code_counts"],
        )


async def _loop() -> None:
    while True:
        try:
            await _run_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - keep transient batch failures from stopping future polls
            # Do not log exception/provider content that could include recipient data.
            logger.warning("Studytomy report delivery poll failed; it will retry.")
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


def start_scheduler() -> None:
    """Start bounded report polling only after an explicit local opt-in."""
    global _task, _worker_id
    if os.environ.get(_ENABLE_ENV) != "true":
        logger.info("Studytomy report delivery scheduler idle; %s is not enabled", _ENABLE_ENV)
        return
    if _task is not None and not _task.done():
        return
    _worker_id = f"report-{uuid4().hex}"
    _task = asyncio.create_task(_loop())
    logger.info("Studytomy report delivery scheduler started with bounded PostgreSQL outbox batches")


async def stop_scheduler() -> None:
    """Stop polling before the application closes its shared database pool."""
    global _task, _worker_id
    task = _task
    _task = None
    _worker_id = None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001 - shutdown must not skip database-pool cleanup
        logger.warning("Studytomy report delivery scheduler stopped after an error")
