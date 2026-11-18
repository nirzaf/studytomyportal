import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.studytomy import report_delivery_scheduler as scheduler
from src.services.studytomy.report_notifications import _delivery_status_for_report


def test_report_scheduler_requires_exact_opt_in(monkeypatch):
    monkeypatch.setattr(scheduler, "_task", None)
    monkeypatch.setattr(scheduler, "_worker_id", None)
    monkeypatch.setenv(scheduler._ENABLE_ENV, "TRUE")

    scheduler.start_scheduler()

    assert scheduler._task is None
    assert scheduler._worker_id is None


def test_report_status_does_not_claim_provider_acceptance_is_delivery():
    assert _delivery_status_for_report("sent") == "provider_accepted"
    assert _delivery_status_for_report("queued") == "queued"
    assert _delivery_status_for_report("suppressed") == "suppressed"
    assert _delivery_status_for_report("reconciliation_required") == "reconciliation_required"


@pytest.mark.asyncio
async def test_report_scheduler_stops_an_inflight_batch(monkeypatch):
    monkeypatch.setattr(scheduler, "_task", None)
    monkeypatch.setattr(scheduler, "_worker_id", None)
    monkeypatch.setenv(scheduler._ENABLE_ENV, "true")
    started = asyncio.Event()

    async def blocked_batch():
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(scheduler, "_run_once", blocked_batch)
    scheduler.start_scheduler()
    task = scheduler._task
    assert task is not None
    await asyncio.wait_for(started.wait(), timeout=1)

    await scheduler.stop_scheduler()

    assert task.done()
    assert scheduler._task is None
    assert scheduler._worker_id is None


@pytest.mark.asyncio
async def test_report_scheduler_retries_without_logging_exception_content(monkeypatch, caplog):
    monkeypatch.setattr(scheduler, "_task", None)
    monkeypatch.setattr(scheduler, "_worker_id", None)
    monkeypatch.setattr(scheduler, "_POLL_INTERVAL_SECONDS", 0.001)
    monkeypatch.setenv(scheduler._ENABLE_ENV, "true")
    second_attempt = asyncio.Event()
    calls = 0

    async def fail_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic recipient address must not be logged")
        second_attempt.set()

    monkeypatch.setattr(scheduler, "_run_once", fail_once)
    scheduler.start_scheduler()
    await asyncio.wait_for(second_attempt.wait(), timeout=1)
    await scheduler.stop_scheduler()

    assert calls >= 2
    assert "synthetic recipient address" not in caplog.text
    assert "poll failed; it will retry" in caplog.text


@pytest.mark.asyncio
async def test_report_queue_summary_returns_only_aggregate_safe_fields():
    now = datetime.now(UTC)
    rows = [
        ("queued", None, 2, now - timedelta(seconds=125)),
        ("sent", None, 3, now - timedelta(days=2)),
        ("reconciliation_required", None, 1, now - timedelta(seconds=600)),
        ("retry", "PROVIDER_TEMPORARILY_UNAVAILABLE", 1, now - timedelta(seconds=40)),
        ("retry", "recipient@example.invalid", 1, now - timedelta(seconds=20)),
        ("retry", "RECIPIENT_SECRET_DO_NOT_LOG", 1, now - timedelta(seconds=10)),
        ("untrusted-status-value", None, 1, now - timedelta(seconds=5)),
    ]
    db_session = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: rows)),
    )

    summary = await scheduler._queue_summary(db_session)

    assert summary["status_counts"] == {
        "queued": 2,
        "provider_accepted": 3,
        "provider_outcome_unknown": 1,
        "retry": 3,
        "unknown": 1,
    }
    assert 120 <= summary["oldest_pending_age_seconds"] < 600
    assert summary["oldest_unknown_delivery_age_seconds"] >= 600
    assert summary["failure_code_counts"] == {"PROVIDER_TEMPORARILY_UNAVAILABLE": 1}
