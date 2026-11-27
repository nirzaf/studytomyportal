import asyncio
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from typer.testing import CliRunner

import cli as portal_cli
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

    summary = await scheduler.report_queue_summary(db_session)

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


@pytest.mark.asyncio
async def test_empty_poll_logs_existing_unknown_outcome_without_private_values(monkeypatch, caplog):
    from src.core.events import database
    from src.services.studytomy import report_notifications

    now = datetime.now(UTC)
    rows = [("reconciliation_required", "recipient@example.invalid", 1, now - timedelta(minutes=10))]
    session = AsyncMock()
    session.__aenter__.return_value = session
    session.execute.return_value = SimpleNamespace(all=lambda: rows)
    monkeypatch.setattr(database, "_async_session_factory", lambda: session)
    monkeypatch.setattr(scheduler, "_worker_id", "fixture-worker")
    monkeypatch.setattr(report_notifications, "run_report_delivery_batch", AsyncMock(return_value={
        "claimed": 0, "sent": 0, "suppressed": 0, "retry": 0,
        "reconciliation_required": 0, "dead_letter": 0,
    }))

    with caplog.at_level(logging.INFO, logger=scheduler.__name__):
        await scheduler._run_once()

    assert "claimed=0" in caplog.text
    assert "'provider_outcome_unknown': 1" in caplog.text
    assert "oldest_unknown_delivery_age_seconds=" in caplog.text
    assert "recipient@example.invalid" not in caplog.text


def _local_cli_config(database: str, *, provider: str = "smtp", smtp_host: str = "127.0.0.1"):
    return SimpleNamespace(
        database_config=SimpleNamespace(sql_connection_string=database),
        mailing_config=SimpleNamespace(email_provider=provider, smtp_host=smtp_host),
    )


def test_report_outbox_drain_defaults_to_aggregate_dry_run(monkeypatch):
    config = _local_cli_config("postgresql://localhost/studytomy_fixture_test")
    monkeypatch.setattr(portal_cli, "get_learnhouse_config", lambda: config)
    monkeypatch.setattr(
        portal_cli, "_read_report_outbox_status",
        lambda: asyncio.sleep(0, result={"status_counts": {"queued": 1}}),
    )
    drain = AsyncMock(return_value={"claimed": 0})
    monkeypatch.setattr(portal_cli, "_drain_report_outbox", drain)

    result = CliRunner().invoke(portal_cli.cli, ["report-outbox-drain", "--limit", "3"])

    assert result.exit_code == 0, result.output
    assert '"mode": "dry_run"' in result.stdout
    assert '"changed": false' in result.stdout
    drain.assert_not_awaited()


def test_report_outbox_status_rejects_nonlocal_targets_before_reading(monkeypatch):
    config = _local_cli_config("postgresql://db.example/studytomy_fixture_test")
    monkeypatch.setattr(portal_cli, "get_learnhouse_config", lambda: config)
    status = AsyncMock(return_value={"status_counts": {}})
    monkeypatch.setattr(portal_cli, "_read_report_outbox_status", status)

    result = CliRunner().invoke(portal_cli.cli, ["report-outbox-status"])

    assert result.exit_code == 2
    status.assert_not_awaited()


def test_report_outbox_drain_executes_one_bounded_batch_only_to_local_sink(monkeypatch):
    config = _local_cli_config("postgresql://localhost/studytomy_fixture_test")
    monkeypatch.setattr(portal_cli, "get_learnhouse_config", lambda: config)
    monkeypatch.setenv("LEARNHOUSE_STUDYTOMY_REPORT_DELIVERY_ENABLED", "true")
    drain = AsyncMock(return_value={"claimed": 2, "sent": 2, "suppressed": 0})
    monkeypatch.setattr(portal_cli, "_drain_report_outbox", drain)

    result = CliRunner().invoke(portal_cli.cli, ["report-outbox-drain", "--limit", "2", "--execute"])

    assert result.exit_code == 0, result.output
    assert '"mode": "executed"' in result.stdout
    drain.assert_awaited_once_with(2)


def test_report_outbox_drain_requires_exact_scheduler_opt_in(monkeypatch):
    config = _local_cli_config("postgresql://localhost/studytomy_fixture_test")
    monkeypatch.setattr(portal_cli, "get_learnhouse_config", lambda: config)
    monkeypatch.setenv("LEARNHOUSE_STUDYTOMY_REPORT_DELIVERY_ENABLED", "TRUE")
    drain = AsyncMock(return_value={"claimed": 0})
    monkeypatch.setattr(portal_cli, "_drain_report_outbox", drain)

    result = CliRunner().invoke(portal_cli.cli, ["report-outbox-drain", "--execute"])

    assert result.exit_code == 2
    drain.assert_not_awaited()


def test_report_outbox_drain_rejects_a_batch_over_the_bound(monkeypatch):
    config = _local_cli_config("postgresql://localhost/studytomy_fixture_test")
    monkeypatch.setattr(portal_cli, "get_learnhouse_config", lambda: config)
    drain = AsyncMock(return_value={"claimed": 0})
    monkeypatch.setattr(portal_cli, "_drain_report_outbox", drain)

    result = CliRunner().invoke(portal_cli.cli, ["report-outbox-drain", "--limit", "26", "--execute"])

    assert result.exit_code != 0
    drain.assert_not_awaited()


@pytest.mark.parametrize(
    "config",
    [
        _local_cli_config("postgresql://localhost/studytomy_fixture"),
        _local_cli_config("postgresql://localhost/studytomy_fixture_test?host=remote.example"),
        _local_cli_config("postgresql://db.example/studytomy_fixture_test"),
        _local_cli_config("mysql://localhost/studytomy_fixture_test"),
        _local_cli_config("postgresql://localhost/studytomy_fixture_test", provider="resend"),
        _local_cli_config("postgresql://localhost/studytomy_fixture_test", smtp_host="mail.example"),
    ],
)
def test_report_outbox_drain_rejects_nonlocal_targets_before_processing(monkeypatch, config):
    monkeypatch.setattr(portal_cli, "get_learnhouse_config", lambda: config)
    monkeypatch.setenv("LEARNHOUSE_STUDYTOMY_REPORT_DELIVERY_ENABLED", "true")
    drain = AsyncMock(return_value={"claimed": 0})
    monkeypatch.setattr(portal_cli, "_drain_report_outbox", drain)

    result = CliRunner().invoke(portal_cli.cli, ["report-outbox-drain", "--execute"])

    assert result.exit_code == 2
    drain.assert_not_awaited()
