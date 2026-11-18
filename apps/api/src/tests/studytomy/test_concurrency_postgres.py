"""Real PostgreSQL proofs for the currently implemented transaction primitives.

These tests intentionally cover only idempotency and outbox behavior already
present in the codebase. Payment, refund, seat, submission and access-revocation
races remain pending their dependent workflows and approvals.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError

from src.db.studytomy.commerce import StudyOffer, StudyOfferVersion
from src.db.studytomy.family import StudyEligibilityDecision, StudyLearnerProfile
from src.db.studytomy.governance import StudyBusinessEvent, StudyOwnerAssignment
from src.db.studytomy.operations import (
    StudyIdempotencyRecord,
    StudyOutboxPurpose,
    StudyOutboxRecord,
)
from src.db.studytomy.privacy import StudyConsentEvent
from src.services.studytomy import (
    idempotency,
    report_delivery_scheduler,
    report_notifications,
)
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.idempotency import claim_idempotency
from src.services.studytomy.outbox import (
    claim_due_deliveries,
    enqueue_delivery,
    release_delivery_lease,
)
from src.services.studytomy.worker import (
    DeliveryAuthorization,
    ProviderOutcomeUnknown,
    ProviderReceipt,
)


def test_runtime_database_variables_are_not_used_as_test_fallbacks():
    from src.tests.studytomy.conftest import _configured_test_url

    runtime_url = "postgresql://user:pass@localhost/learnhouse"
    assert (
        _configured_test_url(
            {
                "DATABASE_URL": runtime_url,
                "LEARNHOUSE_SQL_CONNECTION_STRING": runtime_url,
            }
        )
        is None
    )


@pytest.mark.parametrize(
    "value",
    [
        "sqlite:///./local.db",
        "postgresql://user:pass@db.example.com/studytomy_test",
        "postgresql://user:pass@localhost/learnhouse",
        "postgresql://user:pass@localhost/studytomy_test?host=db.example.com",
    ],
)
def test_postgres_test_url_rejects_nonlocal_or_non_test_targets(value):
    from src.tests.studytomy.conftest import _validated_test_url

    with pytest.raises(pytest.UsageError):
        _validated_test_url(value)


@pytest.mark.studytomy_postgres
async def test_postgres_invariant_guards_reject_mutations(
    postgres_test_rows,
):
    rows = postgres_test_rows
    now = datetime.now(UTC)

    class ExpectedRollback(Exception):
        pass

    async with rows.session_factory() as session:
        with pytest.raises(ExpectedRollback):
            async with session.begin():
                learner = StudyLearnerProfile(
                    org_id=rows.org_id,
                    user_id=rows.user_id,
                    created_by_user_id=rows.user_id,
                    created_at=now,
                    updated_at=now,
                )
                owner = StudyOwnerAssignment(
                    org_id=rows.org_id,
                    user_id=rows.user_id,
                    responsibility="education",
                    scope_kind="organization",
                    scope_uuid=f"org-{rows.org_id}",
                    status="active",
                    evidence_reference="synthetic PostgreSQL guard test",
                    effective_from=now,
                    assigned_by_user_id=rows.user_id,
                    created_at=now,
                )
                offer = StudyOffer(
                    offer_uuid=f"offer-{rows.token}",
                    org_id=rows.org_id,
                    offer_kind="course",
                    status="draft",
                    current_version_no=1,
                    created_by_user_id=rows.user_id,
                    created_at=now,
                )
                session.add_all([learner, owner, offer])
                await session.flush()

                decision = StudyEligibilityDecision(
                    org_id=rows.org_id,
                    learner_profile_id=learner.id,
                    eligibility_kind="learning",
                    outcome="eligible",
                    policy_version_ids=[1],
                    evidence_reference="synthetic PostgreSQL guard test",
                    effective_from=now,
                    reviewer_user_id=rows.user_id,
                    owner_assignment_id=owner.id,
                    created_at=now,
                )
                consent = StudyConsentEvent(
                    org_id=rows.org_id,
                    learner_profile_id=learner.id,
                    subject_user_id=rows.user_id,
                    actor_user_id=rows.user_id,
                    purpose_code="learning_terms",
                    choice="accept",
                    notice_version="synthetic-test-v1",
                    scope_codes=["learning"],
                    policy_version_ids=[1],
                    effective_at=now,
                    created_at=now,
                )
                offer_version = StudyOfferVersion(
                    offer_id=offer.id,
                    version_no=1,
                    terms={"guard_test": True},
                    content_sha256="a" * 64,
                    created_by_user_id=rows.user_id,
                    created_at=now,
                )
                session.add_all([decision, consent, offer_version])
                await session.flush()

                event = await append_business_event(
                    session,
                    org_id=rows.org_id,
                    actor_user_id=rows.user_id,
                    action="postgres_test.created",
                    reason_code="TEST_ONLY",
                    dedupe_key=f"event-{rows.token}",
                )
                mutations = (
                    (
                        update(StudyBusinessEvent)
                        .where(StudyBusinessEvent.id == event.id)
                        .values(action="postgres_test.changed"),
                        "st_business_event rows are immutable",
                    ),
                    (
                        delete(StudyBusinessEvent).where(StudyBusinessEvent.id == event.id),
                        "st_business_event rows are immutable",
                    ),
                    (
                        update(StudyEligibilityDecision)
                        .where(StudyEligibilityDecision.id == decision.id)
                        .values(outcome="ineligible"),
                        "st_eligibility_decision rows are immutable",
                    ),
                    (
                        delete(StudyEligibilityDecision).where(
                            StudyEligibilityDecision.id == decision.id
                        ),
                        "st_eligibility_decision rows are immutable",
                    ),
                    (
                        update(StudyConsentEvent)
                        .where(StudyConsentEvent.id == consent.id)
                        .values(choice="decline"),
                        "st_consent_event rows are immutable",
                    ),
                    (
                        delete(StudyConsentEvent).where(StudyConsentEvent.id == consent.id),
                        "st_consent_event rows are immutable",
                    ),
                    (
                        update(StudyOfferVersion)
                        .where(StudyOfferVersion.id == offer_version.id)
                        .values(content_sha256="b" * 64),
                        "st_offer_version rows are immutable",
                    ),
                    (
                        delete(StudyOfferVersion).where(StudyOfferVersion.id == offer_version.id),
                        "st_offer_version rows are immutable",
                    ),
                    (
                        update(StudyOffer)
                        .where(StudyOffer.id == offer.id)
                        .values(current_version_no=2),
                        "the next st_offer_version must exist",
                    ),
                    (
                        delete(StudyOffer).where(StudyOffer.id == offer.id),
                        "st_offer draft roots are retained",
                    ),
                )
                for mutation, error_message in mutations:
                    with pytest.raises(DBAPIError, match=error_message):
                        async with session.begin_nested():
                            await session.execute(mutation)
                raise ExpectedRollback


@pytest.mark.studytomy_postgres
async def test_idempotency_unique_race_has_one_winner_and_one_conflict(
    postgres_test_rows, monkeypatch
):
    rows = postgres_test_rows
    initial_lookups = 0
    initial_lookups_lock = asyncio.Lock()
    both_lookups_finished = asyncio.Event()
    original_find = idempotency._find

    async def synchronized_initial_find(*args, **kwargs):
        nonlocal initial_lookups
        result = await original_find(*args, **kwargs)
        async with initial_lookups_lock:
            if initial_lookups < 2:
                initial_lookups += 1
                if initial_lookups == 2:
                    both_lookups_finished.set()
                is_initial_lookup = True
            else:
                is_initial_lookup = False
        if is_initial_lookup:
            await both_lookups_finished.wait()
        return result

    monkeypatch.setattr(idempotency, "_find", synchronized_initial_find)

    async def attempt_claim():
        async with rows.session_factory() as session:
            try:
                async with session.begin():
                    result = await claim_idempotency(
                        session,
                        org_id=rows.org_id,
                        actor_user_id=rows.user_id,
                        operation="postgres_test.submit",
                        idempotency_key=f"same-key-{rows.token}",
                        request_payload={"test_case": rows.token},
                    )
                return result
            except HTTPException as error:
                return error

    outcomes = await asyncio.gather(attempt_claim(), attempt_claim())
    successes = [outcome for outcome in outcomes if not isinstance(outcome, HTTPException)]
    conflicts = [outcome for outcome in outcomes if isinstance(outcome, HTTPException)]

    assert len(successes) == 1
    assert successes[0] is None
    assert len(conflicts) == 1
    assert conflicts[0].status_code == 409

    async with rows.session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(StudyIdempotencyRecord)
            .where(
                StudyIdempotencyRecord.org_id == rows.org_id,
                StudyIdempotencyRecord.actor_user_id == rows.user_id,
                StudyIdempotencyRecord.operation == "postgres_test.submit",
            )
        )
    assert count == 1


@pytest.mark.studytomy_postgres
async def test_only_one_request_can_take_over_an_expired_idempotency_lease(
    postgres_test_rows, monkeypatch
):
    rows = postgres_test_rows
    operation = "postgres_test.expired_claim"
    key = f"expired-key-{rows.token}"
    request = {"test_case": rows.token}

    async with rows.session_factory() as session, session.begin():
        await claim_idempotency(
            session,
            org_id=rows.org_id,
            actor_user_id=rows.user_id,
            operation=operation,
            idempotency_key=key,
            request_payload=request,
        )
        record = await session.scalar(
            select(StudyIdempotencyRecord).where(
                StudyIdempotencyRecord.org_id == rows.org_id,
                StudyIdempotencyRecord.actor_user_id == rows.user_id,
                StudyIdempotencyRecord.operation == operation,
            )
        )
        assert record is not None
        record.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    first_lookup_locked = asyncio.Event()
    second_lookup_started = asyncio.Event()
    release_first = asyncio.Event()
    lookup_lock = asyncio.Lock()
    lookup_count = 0
    original_find = idempotency._find

    async def hold_first_lookup(session, *args, **kwargs):
        nonlocal lookup_count
        async with lookup_lock:
            lookup_count += 1
            lookup_number = lookup_count
        if lookup_number == 2:
            second_lookup_started.set()
        result = await original_find(session, *args, **kwargs)
        if lookup_number == 1:
            first_lookup_locked.set()
            await release_first.wait()
        return result

    monkeypatch.setattr(idempotency, "_find", hold_first_lookup)

    async def take_over():
        async with rows.session_factory() as session:
            try:
                async with session.begin():
                    result = await claim_idempotency(
                        session,
                        org_id=rows.org_id,
                        actor_user_id=rows.user_id,
                        operation=operation,
                        idempotency_key=key,
                        request_payload=request,
                    )
                return result
            except HTTPException as error:
                return error

    first_attempt = asyncio.create_task(take_over())
    await asyncio.wait_for(first_lookup_locked.wait(), timeout=10)
    second_attempt = asyncio.create_task(take_over())
    try:
        await asyncio.wait_for(second_lookup_started.wait(), timeout=10)
        await asyncio.sleep(0.05)
        assert not second_attempt.done(), "second claim did not wait for the row lock"
    finally:
        release_first.set()

    outcomes = await asyncio.gather(first_attempt, second_attempt)
    successes = [outcome for outcome in outcomes if outcome is None]
    conflicts = [outcome for outcome in outcomes if isinstance(outcome, HTTPException)]

    assert len(successes) == 1
    assert len(conflicts) == 1
    assert conflicts[0].status_code == 409


@pytest.mark.studytomy_postgres
async def test_idempotency_claim_rolls_back_with_outer_transaction(postgres_test_rows):
    rows = postgres_test_rows

    class ExpectedRollback(Exception):
        pass

    async with rows.session_factory() as session:
        with pytest.raises(ExpectedRollback):
            async with session.begin():
                await claim_idempotency(
                    session,
                    org_id=rows.org_id,
                    actor_user_id=rows.user_id,
                    operation="postgres_test.rollback",
                    idempotency_key=f"rollback-key-{rows.token}",
                    request_payload={"test_case": rows.token},
                )
                raise ExpectedRollback

    async with rows.session_factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(StudyIdempotencyRecord)
            .where(
                StudyIdempotencyRecord.org_id == rows.org_id,
                StudyIdempotencyRecord.actor_user_id == rows.user_id,
                StudyIdempotencyRecord.operation == "postgres_test.rollback",
            )
        )
    assert count == 0


@pytest.mark.studytomy_postgres
async def test_outbox_skip_locked_claim_does_not_double_lease_a_row(postgres_test_rows):
    rows = postgres_test_rows
    async with rows.session_factory() as session:
        await enqueue_delivery(
            session,
            org_id=rows.org_id,
            recipient_user_id=rows.user_id,
            purpose=StudyOutboxPurpose.RECEIPT,
            resource_kind="postgres_test",
            resource_uuid=rows.token,
            dedupe_key=f"outbox-{rows.token}",
            policy_refs=[1],
            available_at=datetime.now(UTC),
        )
        await session.commit()

    first_claimed = asyncio.Event()
    release_first = asyncio.Event()

    async def first_worker():
        async with rows.session_factory() as session, session.begin():
            claimed = await claim_due_deliveries(
                session,
                worker_id=f"worker-a-{rows.token[:12]}",
                limit=1,
            )
            assert len(claimed) == 1
            first_claimed.set()
            await release_first.wait()
            return claimed[0].id

    first_worker_task = asyncio.create_task(first_worker())
    try:
        await asyncio.wait_for(first_claimed.wait(), timeout=10)
        async with rows.session_factory() as session, session.begin():
            second_claim = await asyncio.wait_for(
                claim_due_deliveries(
                    session,
                    worker_id=f"worker-b-{rows.token[:12]}",
                    limit=1,
                ),
                timeout=5,
            )
        assert second_claim == []
    finally:
        release_first.set()
        first_id = await first_worker_task

    async with rows.session_factory() as session:
        record = await session.get(StudyOutboxRecord, first_id)
    assert record is not None
    assert record.status == "leased"
    assert record.lease_owner == f"worker-a-{rows.token[:12]}"
    assert record.attempt_count == 1


@pytest.mark.studytomy_postgres
async def test_outbox_stale_session_cannot_finalize_a_reconciled_lease(postgres_test_rows):
    rows = postgres_test_rows
    started = datetime.now(UTC)
    async with rows.session_factory() as sender:
        record, _ = await enqueue_delivery(
            sender,
            org_id=rows.org_id,
            recipient_user_id=rows.user_id,
            purpose=StudyOutboxPurpose.RECEIPT,
            resource_kind="postgres_test",
            resource_uuid=rows.token,
            dedupe_key=f"outbox-expiry-{rows.token}",
            policy_refs=[1],
            available_at=started,
        )
        worker_id = f"sender-{rows.token[:12]}"
        assert await claim_due_deliveries(
            sender, worker_id=worker_id, now=started, lease_seconds=1
        ) == [record]
        await sender.commit()
        async with rows.session_factory() as recovery, recovery.begin():
            assert await claim_due_deliveries(
                recovery,
                worker_id=f"recovery-{rows.token[:12]}",
                now=started + timedelta(seconds=2),
            ) == []
        assert record.status == "leased"
        with pytest.raises(HTTPException) as denied:
            await release_delivery_lease(
                sender, record, worker_id=worker_id, status_value="sent", now=started
            )
        assert denied.value.status_code == 409
        assert record.status == "reconciliation_required"
        assert record.last_error_code == "LEASE_EXPIRED_OUTCOME_UNKNOWN"
        assert record.provider_message_reference is None


@pytest.mark.studytomy_postgres
async def test_report_delivery_batch_rechecks_authority_and_tracks_provider_outcomes(
    postgres_test_rows, monkeypatch,
):
    rows = postgres_test_rows
    deliveries = {}
    async with rows.session_factory() as session:
        for outcome in ("accepted", "revoked", "unknown"):
            delivery, replay = await enqueue_delivery(
                session,
                org_id=rows.org_id,
                recipient_user_id=rows.user_id,
                purpose=StudyOutboxPurpose.PROGRESS_REPORT,
                resource_kind="progress_report",
                resource_uuid=f"report_{rows.token}_{outcome}",
                resource_version=1,
                authority_reference_uuid=f"grant_{rows.token}",
                policy_refs=[1],
                created_by_user_id=rows.user_id,
                dedupe_key=f"report-{rows.token}-{outcome}",
            )
            assert not replay
            deliveries[outcome] = delivery
        await session.commit()

    async def revalidate(_session, record):
        if record.resource_uuid.endswith("_revoked"):
            return DeliveryAuthorization(
                False, False, False, False, None, "REPORT_RECIPIENT_NOT_AUTHORIZED",
            )
        return DeliveryAuthorization(True, True, True, True, record.resource_version)

    async def send(_session, record, _authorization, _idempotency_key):
        if record.resource_uuid.endswith("_unknown"):
            raise ProviderOutcomeUnknown
        return ProviderReceipt(accepted=True, message_reference=f"synthetic-{rows.token[:12]}")

    send_calls = []

    async def recording_send(*args):
        send_calls.append(args[1].resource_uuid)
        return await send(*args)

    monkeypatch.setattr(report_notifications, "revalidate_report_delivery", revalidate)
    monkeypatch.setattr(report_notifications, "send_report_delivery", recording_send)

    async with rows.session_factory() as session:
        before_summary = await report_delivery_scheduler._queue_summary(session)
        tally = await report_notifications.run_report_delivery_batch(
            session, worker_id=f"report-{rows.token[:16]}", limit=10,
        )
        after_summary = await report_delivery_scheduler._queue_summary(session)

    async with rows.session_factory() as session:
        persisted = {
            outcome: await session.get(StudyOutboxRecord, record.id)
            for outcome, record in deliveries.items()
        }

    assert tally == {
        "claimed": 3, "sent": 1, "suppressed": 1, "retry": 0,
        "reconciliation_required": 1, "dead_letter": 0,
    }
    assert persisted["accepted"].status == "sent"
    assert persisted["accepted"].provider_message_reference == f"synthetic-{rows.token[:12]}"
    assert persisted["revoked"].status == "suppressed"
    assert persisted["revoked"].last_error_code == "REPORT_RECIPIENT_NOT_AUTHORIZED"
    assert persisted["unknown"].status == "reconciliation_required"
    assert persisted["unknown"].last_error_code == "PROVIDER_OUTCOME_UNKNOWN"
    assert persisted["unknown"].provider_message_reference is None
    assert len(send_calls) == 2
    assert all(not resource.endswith("_revoked") for resource in send_calls)
    assert after_summary["status_counts"].get("queued", 0) == before_summary["status_counts"].get("queued", 0) - 3
    for status in ("provider_accepted", "suppressed", "provider_outcome_unknown"):
        assert after_summary["status_counts"].get(status, 0) == before_summary["status_counts"].get(status, 0) + 1
    assert after_summary["failure_code_counts"].get("REPORT_RECIPIENT_NOT_AUTHORIZED", 0) >= 1
    assert after_summary["failure_code_counts"].get("PROVIDER_OUTCOME_UNKNOWN", 0) >= 1
    assert after_summary["oldest_pending_age_seconds"] >= 0


@pytest.mark.studytomy_postgres
async def test_opted_in_scheduler_processes_postgres_outbox_without_manual_batch_call(
    postgres_test_rows, monkeypatch,
):
    rows = postgres_test_rows
    async with rows.session_factory() as session:
        pending_count = (
            await session.execute(
                select(func.count())
                .select_from(StudyOutboxRecord)
                .where(
                    StudyOutboxRecord.purpose
                    == StudyOutboxPurpose.PROGRESS_REPORT.value,
                    StudyOutboxRecord.status.in_(("queued", "retry", "leased")),
                )
            )
        ).scalar_one()
        assert pending_count == 0, "disposable test DB must have no preexisting pending reports"
        delivery, replay = await enqueue_delivery(
            session,
            org_id=rows.org_id,
            recipient_user_id=rows.user_id,
            purpose=StudyOutboxPurpose.PROGRESS_REPORT,
            resource_kind="progress_report",
            resource_uuid=f"report_{rows.token}_scheduler",
            resource_version=1,
            authority_reference_uuid=f"grant_{rows.token}",
            policy_refs=[1],
            created_by_user_id=rows.user_id,
            dedupe_key=f"report-{rows.token}-scheduler",
        )
        assert not replay
        delivery_id = delivery.id
        await session.commit()

    from src.core.events import database as database_events

    monkeypatch.setattr(database_events, "_async_session_factory", rows.session_factory)
    monkeypatch.setattr(report_delivery_scheduler, "_task", None)
    monkeypatch.setattr(report_delivery_scheduler, "_worker_id", None)
    monkeypatch.setattr(report_delivery_scheduler, "_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setenv(report_delivery_scheduler._ENABLE_ENV, "true")
    send_called = asyncio.Event()

    async def revalidate(_session, record):
        assert record.id == delivery_id
        return DeliveryAuthorization(True, True, True, True, record.resource_version)

    async def send(_session, record, _authorization, _idempotency_key):
        assert record.id == delivery_id
        send_called.set()
        return ProviderReceipt(accepted=True, message_reference=f"synthetic-{rows.token[:12]}")

    monkeypatch.setattr(report_notifications, "revalidate_report_delivery", revalidate)
    monkeypatch.setattr(report_notifications, "send_report_delivery", send)
    report_delivery_scheduler.start_scheduler()
    try:
        await asyncio.wait_for(send_called.wait(), timeout=2)
        current_status = None
        for _ in range(100):
            async with rows.session_factory() as session:
                current = await session.get(StudyOutboxRecord, delivery_id)
                current_status = current.status if current else None
            if current_status == "sent":
                break
            await asyncio.sleep(0.01)
    finally:
        await report_delivery_scheduler.stop_scheduler()

    assert current_status == "sent"
