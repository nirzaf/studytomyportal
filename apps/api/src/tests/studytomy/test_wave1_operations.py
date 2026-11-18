"""Wave 1 foundation tests: TASK-004 business events, TASK-005 replay and lock
order, TASK-006 durable delivery, plus the TASK-007/008 fail-closed gates.

Scope honesty: this suite runs on in-memory SQLite. SQLite silently drops
``FOR UPDATE`` (verified against the installed dialect) and this project's test
engine does not turn on foreign-key enforcement, so everything below proves the
*state machine* and the *persistence contract* — what a replay returns, which
outcome a worker records, which row survives a rollback, which value is never
stored. Row locks, skip-locked claims, FK RESTRICT and unique-race winners are
proven only under the ``studytomy_postgres`` marker (TASK-066) and are
deliberately not claimed here.
"""

import hashlib
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import (
    POLICY_REQUIRED_DECISION_AREAS,
    StudyBusinessEvent,
    StudyPolicyBody,
    StudyPolicyDecisionState,
    StudyPolicyDecisionValue,
    StudyPolicyKey,
)
from src.db.studytomy.operations import (
    StudyEvidenceFile,
    StudyIdempotencyRecord,
    StudyIdempotencyStatus,
    StudyOutboxPurpose,
    StudyOutboxRecord,
    StudyOutboxStatus,
)
from src.db.users import User
from src.services.studytomy import concurrency, evidence_files
from src.services.studytomy import worker as worker_service
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.concurrency import (
    LOCK_ORDER,
    LockOrderError,
    acquire_write_locks,
    bump_version,
    lock_rank,
    register_lock_target,
    require_expected_version,
    require_version_match,
    safe_replay_projection,
)
from src.services.studytomy.errors import IDEMPOTENCY_CONFLICT, STALE_VERSION
from src.services.studytomy.evidence_files import _approved_file_rules
from src.services.studytomy.idempotency import (
    LEASE_SECONDS,
    RETENTION_DAYS,
    IdempotencyReplay,
    canonical_request_hash,
    claim_idempotency,
    complete_idempotency,
    release_idempotency_lease,
)
from src.services.studytomy.outbox import (
    claim_due_deliveries,
    enqueue_delivery,
    release_delivery_lease,
)
from src.services.studytomy.readiness import (
    StudyOfferKind,
    evaluate_course_offer_readiness,
)
from src.services.studytomy.worker import (
    _MAX_ATTEMPTS,
    DeliveryAuthorization,
    ProviderOutcomeUnknown,
    ProviderReceipt,
    ProviderTransientFailure,
    RecipientRevalidationFailure,
    run_delivery_batch,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(value: datetime) -> datetime:
    """Treat a read-back value as UTC.

    SQLite stores DATETIME without an offset, so a loaded row hands back a naive
    value while a still-cached instance keeps the aware one. Production reads the
    same column through the service layer's own ``_as_utc`` helper; this mirrors
    it so a comparison cannot depend on which path the row arrived by.
    """
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def _claim(
    db,
    org,
    actor,
    *,
    key: str = "idem-key-1",
    payload=None,
    operation: str = "order.create",
):
    return await claim_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=operation,
        idempotency_key=key,
        request_payload={"amount_minor": 4500, "currency": "GBP"} if payload is None else payload,
    )


async def _only_claim(db, operation: str = "order.create") -> StudyIdempotencyRecord:
    rows = (
        await db.execute(
            select(StudyIdempotencyRecord).where(StudyIdempotencyRecord.operation == operation)
        )
    ).scalars().all()
    assert len(rows) == 1
    return rows[0]


def _authorization(**overrides) -> DeliveryAuthorization:
    defaults = {
        "purpose_allowed": True,
        "consent_current": True,
        "relationship_current": True,
        "resource_complete": True,
        "resource_version": None,
    }
    defaults.update(overrides)
    return DeliveryAuthorization(**defaults)


def _revalidation(**overrides):
    """Build an async revalidation callback with a fixed authority verdict."""

    async def _revalidate(session, record):
        return _authorization(**overrides)

    return _revalidate


def _sending(*, accepted: bool = True, message_reference="prov-1"):
    """Build an async send callback that reports a provider receipt."""

    async def _send(session, record, authorization, provider_key):
        return ProviderReceipt(accepted=accepted, message_reference=message_reference)

    return _send


async def _enqueue(
    db,
    org,
    recipient,
    *,
    key: str = "case_1/notice/v1",
    policy_refs=(7,),
    **overrides,
):
    kwargs = {
        "org_id": org.id,
        "recipient_user_id": recipient.id,
        "purpose": StudyOutboxPurpose.REQUIRED_NOTICE,
        "resource_kind": "case",
        "resource_uuid": "case_1",
        "dedupe_key": key,
        "policy_refs": list(policy_refs),
    }
    kwargs.update(overrides)
    return await enqueue_delivery(db, **kwargs)


class TestIdempotencyReplayProtection:
    """TASK-005: one key means one mutation, and a crash cannot strand a key."""

    async def test_first_claim_returns_control_to_the_caller(self, db, org, admin_user):
        assert await _claim(db, org, admin_user) is None
        record = await _only_claim(db)
        assert record.status == StudyIdempotencyStatus.PROCESSING
        assert record.completed_at is None

    async def test_the_raw_client_key_is_never_persisted(self, db, org, admin_user):
        raw_key = "case_1/submit/2026-11-15"
        await _claim(db, org, admin_user, key=raw_key)
        record = await _only_claim(db)
        assert record.key_hash == hashlib.sha256(raw_key.encode("ascii")).hexdigest()
        stored_values = {
            record.key_hash,
            record.request_hash,
            record.operation,
            record.resource_kind,
            record.resource_uuid,
        }
        assert raw_key not in stored_values
        assert len(record.key_hash) == 64

    async def test_live_lease_refuses_a_concurrent_second_attempt(self, db, org, admin_user):
        await _claim(db, org, admin_user)
        with pytest.raises(HTTPException) as exc:
            await _claim(db, org, admin_user)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == IDEMPOTENCY_CONFLICT
        assert "still running" in exc.value.detail["message"]
        assert exc.value.detail["next_action"]

    async def test_reusing_a_key_for_a_different_request_conflicts(self, db, org, admin_user):
        await _claim(db, org, admin_user)
        with pytest.raises(HTTPException) as exc:
            await _claim(db, org, admin_user, payload={"amount_minor": 9000, "currency": "GBP"})
        assert exc.value.status_code == 409
        assert "different request" in exc.value.detail["message"]

    async def test_completed_result_is_replayed_not_repeated(self, db, org, admin_user):
        await _claim(db, org, admin_user)
        await complete_idempotency(
            db,
            org_id=org.id,
            actor_user_id=admin_user.id,
            operation="order.create",
            idempotency_key="idem-key-1",
            resource_kind="order",
            resource_uuid="order_9",
            response_status=201,
            response_projection={"status": "confirmed"},
        )
        replay = await _claim(db, org, admin_user)
        assert isinstance(replay, IdempotencyReplay)
        assert replay.resource_kind == "order"
        assert replay.resource_uuid == "order_9"
        assert replay.response_status == 201
        assert replay.response_projection == {"status": "confirmed"}
        record = await _only_claim(db)
        assert record.status == StudyIdempotencyStatus.COMPLETED
        assert record.completed_at is not None
        # The same column changes meaning: lease deadline -> replay retention.
        assert _utc(record.expires_at) >= _now() + timedelta(days=RETENTION_DAYS - 1)

    async def test_a_replay_carries_no_mutable_authority(self, db, org, admin_user):
        await _claim(db, org, admin_user)
        await complete_idempotency(
            db,
            org_id=org.id,
            actor_user_id=admin_user.id,
            operation="order.create",
            idempotency_key="idem-key-1",
            resource_kind="order",
            resource_uuid="order_9",
        )
        replay = await _claim(db, org, admin_user)
        assert set(replay.model_dump()) == {
            "resource_kind",
            "resource_uuid",
            "response_status",
            "response_projection",
        }

    async def test_absent_key_performs_no_guard(self, db, org, admin_user):
        assert await _claim(db, org, admin_user, key=None) is None
        assert (await db.execute(select(StudyIdempotencyRecord))).scalars().all() == []

    @pytest.mark.parametrize(
        "raw_key",
        ["", "   ", "a" * 256, "key with spaces", "keÿ-non-ascii"],
    )
    async def test_malformed_keys_are_rejected_before_any_row(self, db, org, admin_user, raw_key):
        with pytest.raises(HTTPException) as exc:
            await _claim(db, org, admin_user, key=raw_key)
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == IDEMPOTENCY_CONFLICT
        assert exc.value.detail["field_errors"] == {"Idempotency-Key": "Invalid format"}
        assert (await db.execute(select(StudyIdempotencyRecord))).scalars().all() == []

    async def test_expired_lease_is_taken_over_not_stranded(self, db, org, admin_user):
        await _claim(db, org, admin_user)
        record = await _only_claim(db)
        record.expires_at = _now() - timedelta(seconds=1)
        await db.flush()

        assert await _claim(db, org, admin_user) is None
        reclaimed = await _only_claim(db)
        assert reclaimed.status == StudyIdempotencyStatus.PROCESSING
        assert _utc(reclaimed.expires_at) > _now()

    async def test_released_lease_makes_the_key_retryable(self, db, org, admin_user):
        await _claim(db, org, admin_user)
        with pytest.raises(HTTPException):
            await _claim(db, org, admin_user)
        await release_idempotency_lease(
            db,
            org_id=org.id,
            actor_user_id=admin_user.id,
            operation="order.create",
            idempotency_key="idem-key-1",
        )
        assert await _claim(db, org, admin_user) is None

    async def test_completing_an_unclaimed_key_is_refused(self, db, org, admin_user):
        with pytest.raises(HTTPException) as exc:
            await complete_idempotency(
                db,
                org_id=org.id,
                actor_user_id=admin_user.id,
                operation="order.create",
                idempotency_key="never-claimed",
                resource_kind="order",
                resource_uuid="order_1",
            )
        assert exc.value.status_code == 409

    def test_request_hash_is_semantic_not_textual(self):
        assert canonical_request_hash({"a": 1, "b": 2}) == canonical_request_hash({"b": 2, "a": 1})
        assert canonical_request_hash({"a": 1}) != canonical_request_hash({"a": 2})

    async def test_lease_bound_is_an_engineering_limit_not_a_promise(self, db, org, admin_user):
        await _claim(db, org, admin_user)
        record = await _only_claim(db)
        assert _utc(record.expires_at) <= _now() + timedelta(seconds=LEASE_SECONDS)


class TestBusinessEventAtomicity:
    """TASK-004: the event ledger shares its caller's transaction, always."""

    async def test_event_and_mutation_rollback_together(self, db, org, admin_user):
        # SQLite legacy transaction control does not begin a DB transaction for
        # SAVEPOINT alone. Start the outer transaction so RELEASE remains
        # rollbackable, matching the PostgreSQL transaction contract.
        # This demonstrates the service never commits on its own; TASK-066 owns
        # the authoritative caller-managed rollback proof under PostgreSQL.
        await db.execute(text("BEGIN"))
        await _claim(db, org, admin_user)
        event = await append_business_event(
            db,
            org_id=org.id,
            actor_user_id=admin_user.id,
            action="order.place.requested",
            reason_code="PILOT_ORDER_REQUEST",
            resource_kind="order",
            resource_uuid="order_7",
            before_state={"status": None},
            after_state={"status": "confirmed"},
            policy_refs=[1, 2],
            correlation_id="corr-1",
        )
        assert event.id is not None
        await db.rollback()
        assert (await db.execute(select(StudyBusinessEvent))).scalars().all() == []
        assert (await db.execute(select(StudyIdempotencyRecord))).scalars().all() == []

    async def test_committed_event_is_readable_by_resource(self, db, org, admin_user):
        await append_business_event(
            db,
            org_id=org.id,
            actor_user_id=admin_user.id,
            action="refund.decision.recorded",
            reason_code="HUMAN_APPROVAL",
            actor_capacity="finance_approver",
            resource_kind="refund_request",
            resource_uuid="refund_3",
            after_state={"decision_code": "approved"},
        )
        await db.commit()
        rows = (
            await db.execute(
                select(StudyBusinessEvent).where(
                    StudyBusinessEvent.org_id == org.id,
                    StudyBusinessEvent.resource_uuid == "refund_3",
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].reason == "HUMAN_APPROVAL"
        assert rows[0].policy_refs is None or isinstance(rows[0].policy_refs, list)

    async def test_a_rejected_event_aborts_the_accompanying_decision(self, db, org, admin_user):
        org_id = org.id
        org.description = "money confirmed"
        db.add(org)
        with pytest.raises(HTTPException) as exc:
            await append_business_event(
                db,
                org_id=org.id,
                actor_user_id=admin_user.id,
                action="refund settled",
                reason_code="HUMAN_APPROVAL",
            )
        assert exc.value.status_code == 422
        await db.rollback()
        stored = (
            await db.execute(select(Organization).where(Organization.id == org_id))
        ).scalars().one()
        assert stored.description != "money confirmed"

    async def test_exactly_one_actor_is_required(self, db, org, admin_user):
        with pytest.raises(HTTPException) as both:
            await append_business_event(
                db,
                org_id=org.id,
                actor_user_id=admin_user.id,
                actor_machine_id="worker-1",
                action="order.place.requested",
                reason_code="PILOT_ORDER_REQUEST",
            )
        assert both.value.status_code == 422
        with pytest.raises(HTTPException) as neither:
            await append_business_event(
                db,
                org_id=org.id,
                actor_user_id=None,
                action="order.place.requested",
                reason_code="PILOT_ORDER_REQUEST",
            )
        assert neither.value.status_code == 422

    async def test_machine_actor_is_supported(self, db, org, admin_user):
        event = await append_business_event(
            db,
            org_id=org.id,
            actor_user_id=None,
            actor_machine_id="delivery-worker-1",
            action="delivery.outbox.claimed",
            reason_code="WORKER_BATCH",
        )
        assert event.actor_user_id is None
        assert event.actor_machine_id == "delivery-worker-1"

    @pytest.mark.parametrize("action", ["grade", "Grade.Set", "grade set", "1grade.set"])
    async def test_unsafe_action_codes_are_refused(self, db, org, admin_user, action):
        with pytest.raises(HTTPException) as exc:
            await append_business_event(
                db,
                org_id=org.id,
                actor_user_id=admin_user.id,
                action=action,
                reason_code="HUMAN_APPROVAL",
            )
        assert exc.value.status_code == 422

    @pytest.mark.parametrize("reason_code", ["ok", "lower_case", "A", "reason code"])
    async def test_unsafe_reason_codes_are_refused(self, db, org, admin_user, reason_code):
        with pytest.raises(HTTPException) as exc:
            await append_business_event(
                db,
                org_id=org.id,
                actor_user_id=admin_user.id,
                action="order.place.requested",
                reason_code=reason_code,
            )
        assert exc.value.status_code == 422

    async def test_sensitive_and_unapproved_state_keys_are_refused(self, db, org, admin_user):
        for state in (
            {"email": "family@example.test"},
            {"answer_key": "4"},
            {"chat": "transcript"},
            {"unapproved_field": 1},
            {"status": {"nested": "object"}},
            {"status": "x" * 201},
        ):
            with pytest.raises(HTTPException) as exc:
                await append_business_event(
                    db,
                    org_id=org.id,
                    actor_user_id=admin_user.id,
                    action="learner.profile.updated",
                    reason_code="PROFILE_CORRECTION",
                    after_state=state,
                )
            assert exc.value.status_code == 422

    async def test_policy_references_must_be_positive_ids(self, db, org, admin_user):
        for refs in ([0], [-3], ["1"], list(range(1, 22))):
            with pytest.raises(HTTPException) as exc:
                await append_business_event(
                    db,
                    org_id=org.id,
                    actor_user_id=admin_user.id,
                    action="policy.version.approved",
                    reason_code="HUMAN_APPROVAL",
                    policy_refs=refs,
                )
            assert exc.value.status_code == 422

    async def test_duplicate_dedupe_key_cannot_record_the_same_fact_twice(
        self, db, org, admin_user
    ):
        await append_business_event(
            db,
            org_id=org.id,
            actor_user_id=admin_user.id,
            action="order.place.requested",
            reason_code="PILOT_ORDER_REQUEST",
            dedupe_key="order_7/requested",
        )
        with pytest.raises(IntegrityError):
            await append_business_event(
                db,
                org_id=org.id,
                actor_user_id=admin_user.id,
                action="order.place.requested",
                reason_code="PILOT_ORDER_REQUEST",
                dedupe_key="order_7/requested",
            )


class TestCanonicalLockOrder:
    """TASK-005: one order, one helper, and a caller may never re-sort it."""

    def test_the_documented_order_is_the_only_order(self):
        assert LOCK_ORDER == (
            "capacity",
            "order_line",
            "membership_cycle",
            "collection",
            "refund_reservation",
            "entitlement",
        )
        assert [lock_rank(kind) for kind in LOCK_ORDER] == list(range(len(LOCK_ORDER)))

    def test_an_unknown_kind_is_a_programming_error(self):
        with pytest.raises(LockOrderError):
            lock_rank("user_session")

    async def test_a_reversed_request_is_refused_before_any_lock_is_taken(
        self, db, org, monkeypatch
    ):
        monkeypatch.setattr(concurrency, "_LOCK_TARGETS", {"capacity": Organization})
        with pytest.raises(LockOrderError, match="Lock order violation"):
            await acquire_write_locks(db, [("collection", 1), ("capacity", org.id)])

    async def test_targets_in_order_are_locked(self, db, org, monkeypatch):
        monkeypatch.setattr(concurrency, "_LOCK_TARGETS", {"capacity": Organization})
        await acquire_write_locks(db, [("capacity", org.id), ("capacity", org.id)])

    async def test_a_missing_row_stops_the_write(self, db, org, monkeypatch):
        monkeypatch.setattr(concurrency, "_LOCK_TARGETS", {"capacity": Organization})
        with pytest.raises(HTTPException) as exc:
            await acquire_write_locks(db, [("capacity", 987654)])
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == STALE_VERSION

    async def test_an_unregistered_kind_cannot_silently_skip_locking(self, db, monkeypatch):
        monkeypatch.setattr(concurrency, "_LOCK_TARGETS", {})
        with pytest.raises(LockOrderError, match="no registered table"):
            await acquire_write_locks(db, [("entitlement", 1)])

    async def test_a_row_id_must_be_positive(self, db, monkeypatch):
        monkeypatch.setattr(concurrency, "_LOCK_TARGETS", {})
        with pytest.raises(LockOrderError, match="positive row id"):
            await acquire_write_locks(db, [("capacity", 0)])

    async def test_no_locks_requested_is_a_no_op(self, db):
        await acquire_write_locks(db, [])

    def test_one_kind_cannot_be_bound_to_two_tables(self, monkeypatch):
        monkeypatch.setattr(concurrency, "_LOCK_TARGETS", {})
        register_lock_target("capacity", Organization)
        with pytest.raises(LockOrderError, match="already bound"):
            register_lock_target("capacity", Course)
        with pytest.raises(LockOrderError, match="Unknown lock kind"):
            register_lock_target("classroom", Organization)


class TestStaleWriteGuards:
    """TASK-005: legacy editor behaviour preserved, governed rows use coded 409."""

    def test_optional_expected_version_never_rejects(self):
        require_expected_version("Draft", None, 9)
        require_expected_version("Draft", 9, 9)

    def test_legacy_stale_write_detail_shape_is_unchanged(self):
        with pytest.raises(HTTPException) as exc:
            require_expected_version("Draft", 3, 5)
        assert exc.value.status_code == 409
        assert isinstance(exc.value.detail, str)
        assert "STALE_WRITE" in exc.value.detail
        assert "current version is 5" in exc.value.detail

    def test_governed_rows_use_the_coded_contract(self):
        require_version_match(4, 4)
        with pytest.raises(HTTPException) as stale:
            require_version_match(4, 3, resource_label="relationship")
        assert stale.value.status_code == 409
        assert stale.value.detail["code"] == STALE_VERSION
        assert stale.value.detail["next_action"]

    def test_governed_writes_must_declare_the_version_they_read(self):
        with pytest.raises(HTTPException) as exc:
            require_version_match(4, None, resource_label="policy")
        assert exc.value.status_code == 422
        assert exc.value.detail["field_errors"] == {"expected_version": "Required"}

    def test_row_versions_start_at_one(self):
        assert bump_version(1) == 2
        with pytest.raises(LockOrderError):
            bump_version(0)

    def test_replay_projection_is_whitelisted(self):
        row = SimpleNamespace(status="active", resource_uuid="r-1", email="a@b.test")
        assert safe_replay_projection(row, ("status", "resource_uuid")) == {
            "status": "active",
            "resource_uuid": "r-1",
        }
        assert safe_replay_projection(row, ("missing",)) == {"missing": None}


class TestOutboxEnqueue:
    """TASK-006: delivery intent is recorded as opaque, replayable intent only."""

    async def test_enqueue_writes_opaque_intent_and_no_content(self, db, org, regular_user):
        record, replayed = await _enqueue(db, org, regular_user)
        assert replayed is False
        assert record.status == StudyOutboxStatus.QUEUED
        assert record.attempt_count == 0
        assert record.template_key == "required_notice_v1"
        assert record.policy_refs == [7]
        assert record.dedupe_hash == hashlib.sha256(b"case_1/notice/v1").hexdigest()
        assert "case_1/notice/v1" not in record.dedupe_hash + record.request_hash
        assert len(record.request_hash) == 64
        assert record.completed_at is None
        # Generated once so a retry of this row is never a second message.
        assert record.provider_idempotency_key

    async def test_same_dedupe_key_replays_one_row(self, db, org, regular_user):
        first, _ = await _enqueue(db, org, regular_user)
        second, replayed = await _enqueue(db, org, regular_user)
        assert replayed is True
        assert second.id == first.id
        assert len((await db.execute(select(StudyOutboxRecord))).scalars().all()) == 1

    async def test_reusing_a_key_for_other_intent_conflicts(self, db, org, regular_user):
        await _enqueue(db, org, regular_user)
        with pytest.raises(HTTPException) as exc:
            await _enqueue(db, org, regular_user, policy_refs=(9,))
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "DELIVERY_DEDUPE_KEY_REUSED"

    async def test_policy_references_are_mandatory_and_bounded(self, db, org, regular_user):
        for refs in ([], ["1"], [0], [True]):
            with pytest.raises(HTTPException) as exc:
                await _enqueue(db, org, regular_user, policy_refs=refs)
            assert exc.value.status_code == 409

    async def test_optional_purposes_require_a_recipient_authority(self, db, org, regular_user):
        for purpose in (
            StudyOutboxPurpose.PROGRESS_REPORT,
            StudyOutboxPurpose.OPTIONAL_REMINDER,
            StudyOutboxPurpose.MARKETING,
        ):
            with pytest.raises(HTTPException) as exc:
                await _enqueue(db, org, regular_user, purpose=purpose, key=f"{purpose.value}/1")
            assert exc.value.status_code == 409
            assert "authority" in exc.value.detail

    async def test_unknown_purpose_is_not_accepted(self, db, org, regular_user):
        with pytest.raises(HTTPException) as exc:
            await _enqueue(db, org, regular_user, purpose="push_notification")
        assert exc.value.status_code == 422

    async def test_naive_availability_is_refused(self, db, org, regular_user):
        with pytest.raises(HTTPException) as exc:
            await _enqueue(db, org, regular_user, available_at=datetime(2026, 11, 15, 9, 0))
        assert exc.value.status_code == 422

    async def test_unbounded_resource_reference_is_refused(self, db, org, regular_user):
        with pytest.raises(HTTPException) as exc:
            await _enqueue(db, org, regular_user, resource_uuid="x" * 101)
        assert exc.value.status_code == 422

    async def test_unknown_organization_is_not_queued(self, db, regular_user):
        with pytest.raises(HTTPException) as exc:
            await enqueue_delivery(
                db,
                org_id=999_999,
                recipient_user_id=regular_user.id,
                purpose=StudyOutboxPurpose.REQUIRED_NOTICE,
                resource_kind="case",
                resource_uuid="case_1",
                dedupe_key="orphan/1",
                policy_refs=[7],
            )
        assert exc.value.status_code == 404


class TestOutboxClaimAndRelease:
    """TASK-006: leases, expiry and outcome recording — not parallelism."""

    async def test_due_rows_are_leased_once(self, db, org, regular_user):
        record, _ = await _enqueue(db, org, regular_user)
        claimed = await claim_due_deliveries(db, worker_id="worker-a", limit=5)
        assert [row.id for row in claimed] == [record.id]
        assert record.status == StudyOutboxStatus.LEASED
        assert record.attempt_count == 1
        assert record.lease_owner == "worker-a"
        assert _utc(record.lease_expires_at) > _now()
        # A leased row is not re-queued: a second claim finds nothing to do.
        assert await claim_due_deliveries(db, worker_id="worker-b", limit=5) == []

    async def test_future_delivery_waits(self, db, org, regular_user):
        await _enqueue(db, org, regular_user, available_at=_now() + timedelta(hours=1))
        assert await claim_due_deliveries(db, worker_id="worker-a", limit=5) == []

    async def test_expired_lease_becomes_reconciliation_never_resent(self, db, org, regular_user):
        started = _now()
        record, _ = await _enqueue(db, org, regular_user, available_at=started)
        claimed = await claim_due_deliveries(
            db, worker_id="worker-a", limit=5, now=started, lease_seconds=30
        )
        assert len(claimed) == 1

        later = started + timedelta(minutes=5)
        reclaimed = await claim_due_deliveries(db, worker_id="worker-b", limit=5, now=later)
        assert reclaimed == []
        assert record.status == StudyOutboxStatus.RECONCILIATION_REQUIRED
        assert record.last_error_code == "LEASE_EXPIRED_OUTCOME_UNKNOWN"
        assert record.lease_owner is None
        assert record.completed_at is None
        assert record.attempt_count == 1

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"worker_id": ""},
            {"worker_id": "w" * 101},
            {"worker_id": "bad worker"},
            {"limit": 0},
            {"limit": 101},
            {"lease_seconds": 0},
            {"lease_seconds": 901},
        ],
    )
    async def test_claim_bounds_are_enforced(self, db, org, regular_user, kwargs):
        await _enqueue(db, org, regular_user)
        call = {"worker_id": "worker-a", "limit": 5, **kwargs}
        with pytest.raises(HTTPException) as exc:
            await claim_due_deliveries(db, **call)
        assert exc.value.status_code == 422

    async def test_only_the_lease_owner_may_record_an_outcome(self, db, org, regular_user):
        record, _ = await _enqueue(db, org, regular_user)
        await claim_due_deliveries(db, worker_id="worker-a", limit=5)
        with pytest.raises(HTTPException) as exc:
            await release_delivery_lease(
                db, record, worker_id="worker-b", status_value="sent", now=_now()
            )
        assert exc.value.status_code == 409

    async def test_a_sent_outcome_clears_the_lease(self, db, org, regular_user):
        record, _ = await _enqueue(db, org, regular_user)
        await claim_due_deliveries(db, worker_id="worker-a", limit=5)
        await release_delivery_lease(
            db,
            record,
            worker_id="worker-a",
            status_value="sent",
            now=_now(),
            provider_message_reference="prov:abc-123",
        )
        assert record.status == StudyOutboxStatus.SENT
        assert record.lease_owner is None
        assert record.completed_at is not None
        assert record.provider_message_reference == "prov:abc-123"

    async def test_release_normalizes_sqlite_naive_lease_after_reload(self, db, org, regular_user):
        started = _now()
        record, _ = await _enqueue(db, org, regular_user, available_at=started)
        await claim_due_deliveries(
            db, worker_id="worker-a", limit=5, now=started, lease_seconds=60
        )
        await db.commit()
        await db.refresh(record, attribute_names=["lease_expires_at"])
        assert record.lease_expires_at is not None
        assert record.lease_expires_at.tzinfo is None

        await release_delivery_lease(
            db,
            record,
            worker_id="worker-a",
            status_value="sent",
            now=started + timedelta(seconds=1),
        )

        assert record.status == StudyOutboxStatus.SENT

    async def test_outcome_arguments_are_validated(self, db, org, regular_user):
        record, _ = await _enqueue(db, org, regular_user)
        await claim_due_deliveries(db, worker_id="worker-a", limit=5)
        cases = [
            ({"status_value": "delivered"}, "Invalid delivery outcome"),
            ({"status_value": "retry"}, "retry time is required"),
            ({"status_value": "sent", "error_code": "lower case"}, "stable safe code"),
            (
                {"status_value": "sent", "provider_message_reference": "has space"},
                "provider reference",
            ),
            (
                {
                    "status_value": "retry",
                    "retry_at": datetime(2026, 11, 15, 9, 0),
                },
                "retry_at must include a timezone",
            ),
        ]
        for overrides, needle in cases:
            with pytest.raises(HTTPException) as exc:
                await release_delivery_lease(
                    db, record, worker_id="worker-a", now=_now(), **overrides
                )
            assert exc.value.status_code == 422, overrides
            assert needle in str(exc.value.detail)


class TestDeliveryWorker:
    """TASK-006: revalidate immediately before sending, and never claim a lie."""

    async def test_allowed_delivery_is_sent_once(self, db, org, regular_user):
        record, _ = await _enqueue(db, org, regular_user)
        seen_keys = []

        async def send(session, claimed, authorization, provider_key):
            seen_keys.append(provider_key)
            return ProviderReceipt(accepted=True, message_reference="prov-1")

        outcomes = await run_delivery_batch(
            db,
            worker_id="worker-a",
            revalidate=_revalidation(),
            send=send,
            limit=5,
        )
        assert outcomes["claimed"] == 1 and outcomes["sent"] == 1
        assert record.status == StudyOutboxStatus.SENT
        assert seen_keys == [record.provider_idempotency_key]

    async def test_worker_normalizes_sqlite_naive_lease_after_reload(
        self, db, org, regular_user, monkeypatch
    ):
        started = _now()
        record, _ = await _enqueue(db, org, regular_user, available_at=started)
        await claim_due_deliveries(
            db, worker_id="worker-a", limit=5, now=started, lease_seconds=60
        )
        await db.commit()
        await db.refresh(record, attribute_names=["lease_expires_at"])
        assert record.lease_expires_at is not None
        assert record.lease_expires_at.tzinfo is None

        async def return_reloaded_claim(*args, **kwargs):
            return [record]

        monkeypatch.setattr(
            worker_service, "claim_due_deliveries", return_reloaded_claim
        )
        outcomes = await run_delivery_batch(
            db,
            worker_id="worker-a",
            revalidate=_revalidation(),
            send=_sending(),
            limit=5,
            now=started + timedelta(seconds=1),
        )

        assert outcomes["sent"] == 1
        assert record.status == StudyOutboxStatus.SENT

    async def test_revoked_authority_suppresses_before_any_send(self, db, org, regular_user):
        record, _ = await _enqueue(db, org, regular_user)
        calls = []

        async def send(*args):
            calls.append(args)
            return ProviderReceipt(accepted=True)

        outcomes = await run_delivery_batch(
            db,
            worker_id="worker-a",
            revalidate=_revalidation(
                purpose_allowed=False,
                consent_current=False,
                denial_code="RECIPIENT_CONSENT_WITHDRAWN",
            ),
            send=send,
            limit=5,
        )
        assert outcomes["suppressed"] == 1 and outcomes["sent"] == 0
        assert calls == []
        assert record.status == StudyOutboxStatus.SUPPRESSED
        assert record.last_error_code == "RECIPIENT_CONSENT_WITHDRAWN"

    async def test_an_unsafe_denial_code_is_normalised(self, db, org, regular_user):
        record, _ = await _enqueue(db, org, regular_user)
        await run_delivery_batch(
            db,
            worker_id="worker-a",
            revalidate=_revalidation(
                relationship_current=False, denial_code="Guardian Sarah objected"
            ),
            send=_sending(),
            limit=5,
        )
        assert record.last_error_code == "RECIPIENT_AUTHORIZATION_DENIED"

    async def test_changed_resource_version_is_not_disclosed(self, db, org, regular_user):
        record, _ = await _enqueue(db, org, regular_user, resource_version=3)
        outcomes = await run_delivery_batch(
            db,
            worker_id="worker-a",
            revalidate=_revalidation(resource_version=4),
            send=_sending(),
            limit=5,
        )
        assert outcomes["suppressed"] == 1
        assert record.last_error_code == "RESOURCE_VERSION_CHANGED"

    async def test_a_retry_reuses_the_provider_idempotency_key(self, db, org, regular_user):
        record, _ = await _enqueue(db, org, regular_user)
        seen = []

        async def failing_send(session, claimed, authorization, provider_key):
            seen.append(provider_key)
            raise ProviderTransientFailure("provider is down")

        outcomes = await run_delivery_batch(
            db,
            worker_id="worker-a",
            revalidate=_revalidation(),
            send=failing_send,
            limit=5,
        )
        assert outcomes["retry"] == 1
        assert record.status == StudyOutboxStatus.RETRY
        assert record.completed_at is None
        assert _utc(record.next_attempt_at) > _now()

        # Once the backoff window has passed the same key must be reused.
        record.next_attempt_at = _now() - timedelta(seconds=1)
        await db.flush()

        async def working_send(session, claimed, authorization, provider_key):
            seen.append(provider_key)
            return ProviderReceipt(accepted=True, message_reference="prov-2")

        outcomes = await run_delivery_batch(
            db,
            worker_id="worker-a",
            revalidate=_revalidation(),
            send=working_send,
            limit=5,
        )
        assert outcomes["sent"] == 1
        assert set(seen) == {record.provider_idempotency_key}

    async def test_retries_are_capped_into_a_dead_letter(self, db, org, regular_user):
        record, _ = await _enqueue(db, org, regular_user)
        record.attempt_count = _MAX_ATTEMPTS - 1
        await db.flush()

        async def failing_send(session, claimed, authorization, provider_key):
            raise ProviderTransientFailure("still down")

        outcomes = await run_delivery_batch(
            db,
            worker_id="worker-a",
            revalidate=_revalidation(),
            send=failing_send,
            limit=5,
        )
        assert outcomes["dead_letter"] == 1
        assert record.status == StudyOutboxStatus.DEAD_LETTER
        assert record.completed_at is not None

    async def test_an_uncertain_outcome_is_never_shown_as_delivered(self, db, org, regular_user):
        record, _ = await _enqueue(db, org, regular_user)

        async def unknown_send(session, claimed, authorization, provider_key):
            raise ProviderOutcomeUnknown("timed out after handoff")

        outcomes = await run_delivery_batch(
            db,
            worker_id="worker-a",
            revalidate=_revalidation(),
            send=unknown_send,
            limit=5,
        )
        assert outcomes["reconciliation_required"] == 1
        assert record.status == StudyOutboxStatus.RECONCILIATION_REQUIRED
        assert record.last_error_code == "PROVIDER_OUTCOME_UNKNOWN"
        assert record.completed_at is None
        # Reconciliation is not a queue state: no automatic resend.
        assert await claim_due_deliveries(db, worker_id="worker-b", limit=5) == []

    @pytest.mark.parametrize(("accepted", "reference"), [
        (False, None),
        (True, 42),
        (True, "unsafe reference"),
        ("true", None),
    ])
    async def test_an_unverifiable_receipt_is_uncertain_not_sent(
        self, db, org, regular_user, accepted, reference,
    ):
        record, _ = await _enqueue(db, org, regular_user)
        outcomes = await run_delivery_batch(
            db,
            worker_id="worker-a",
            revalidate=_revalidation(),
            send=_sending(accepted=accepted, message_reference=reference),
            limit=5,
        )
        assert outcomes["reconciliation_required"] == 1
        assert record.status == StudyOutboxStatus.RECONCILIATION_REQUIRED
        assert record.last_error_code == "PROVIDER_OUTCOME_UNKNOWN"
        assert await claim_due_deliveries(db, worker_id="worker-b", limit=5) == []

    async def test_a_revalidation_bug_cannot_crash_the_batch(self, db, org, regular_user):
        record, _ = await _enqueue(db, org, regular_user)
        calls = []

        async def send(*args):
            calls.append(args)
            return ProviderReceipt(accepted=True)

        async def broken_revalidation(session, claimed):
            raise RuntimeError("database exploded while checking consent")

        outcomes = await run_delivery_batch(
            db,
            worker_id="worker-a",
            revalidate=broken_revalidation,
            send=send,
            limit=5,
        )
        assert calls == []
        assert outcomes["retry"] == 1
        assert record.last_error_code == "RECIPIENT_REVALIDATION_FAILURE"

    async def test_unavailable_revalidation_is_retried_not_suppressed(self, db, org, regular_user):
        record, _ = await _enqueue(db, org, regular_user)

        async def unavailable(session, claimed):
            raise RecipientRevalidationFailure("consent store unreachable")

        outcomes = await run_delivery_batch(
            db,
            worker_id="worker-a",
            revalidate=unavailable,
            send=_sending(),
            limit=5,
        )
        assert outcomes["retry"] == 1 and outcomes["suppressed"] == 0
        assert record.last_error_code == "RECIPIENT_REVALIDATION_UNAVAILABLE"

    async def test_an_empty_batch_is_not_an_error(self, db):
        outcomes = await run_delivery_batch(
            db,
            worker_id="worker-a",
            revalidate=_revalidation(),
            send=_sending(),
            limit=5,
        )
        assert outcomes == {
            "claimed": 0,
            "sent": 0,
            "suppressed": 0,
            "retry": 0,
            "reconciliation_required": 0,
            "dead_letter": 0,
        }


class TestEvidenceFileGuards:
    """TASK-007: unapproved storage policy fails closed; rows keep integrity."""

    async def test_evidence_download_checks_authority_before_attachment_lookup(
        self, db, org, admin_user, regular_user
    ):
        profile = StudyLearnerProfile(
            org_id=org.id, user_id=regular_user.id, created_by_user_id=admin_user.id
        )
        db.add(profile)
        await db.flush()
        evidence_uuid = f"ev_{uuid4()}"
        db.add(StudyEvidenceFile(
            evidence_uuid=evidence_uuid,
            org_id=org.id,
            learner_profile_id=profile.id,
            purpose="eligibility_evidence",
            storage_reference=f"content/orgs/{org.org_uuid}/studytomy/evidence/{evidence_uuid}.pdf",
            content_sha256="a" * 64,
            policy_version_ids=[1],
            media_type="application/pdf",
            size_bytes=64,
            retention_category="learner_evidence_core",
            upload_status="ready",
            uploaded_by_user_id=admin_user.id,
        ))
        await db.commit()
        with patch.object(evidence_files, "open_content_stream", new_callable=AsyncMock) as storage:
            responses = []
            for requested_uuid in (evidence_uuid, f"ev_{uuid4()}"):
                with pytest.raises(HTTPException) as denied:
                    await evidence_files.open_eligibility_evidence(
                        db, org, regular_user, profile.learner_uuid, requested_uuid
                    )
                responses.append((denied.value.status_code, denied.value.detail))
            assert responses[0] == responses[1]
            storage.assert_not_awaited()

    async def test_lost_commit_acknowledgment_preserves_committed_file(
        self, db, org, admin_user, regular_user
    ):
        await self._exercise_lost_commit_acknowledgment(
            db, db, org, admin_user, regular_user
        )

    @pytest.mark.studytomy_postgres
    async def test_lost_commit_acknowledgment_replays_in_a_separate_postgres_session(
        self, postgres_session_factory
    ):
        token = uuid4().hex
        org = Organization(
            name="Synthetic evidence upload",
            slug=f"evidence-{token}",
            org_uuid=f"org_{token}",
            email=f"org-{token}@example.invalid",
        )
        users = [
            User(
                username=f"{role}-{token}",
                user_uuid=f"user_{role}_{token}",
                email=f"{role}-{token}@example.invalid",
                first_name="Synthetic",
                last_name="Fixture",
            )
            for role in ("staff", "learner")
        ]
        async with postgres_session_factory() as db, postgres_session_factory() as retry_db:
            db.add_all([org, *users])
            await db.commit()
            await self._exercise_lost_commit_acknowledgment(db, retry_db, org, *users)

    @staticmethod
    async def _exercise_lost_commit_acknowledgment(db, retry_db, org, admin_user, regular_user):
        profile = StudyLearnerProfile(
            org_id=org.id, user_id=regular_user.id, created_by_user_id=admin_user.id
        )
        db.add(profile)
        await db.commit()
        real_commit = db.commit

        async def lose_acknowledgment():
            await real_commit()
            raise ConnectionError("Commit acknowledgment lost")

        async def upload(session):
            with BytesIO(b"%PDF-1.7\nSynthetic evidence") as content:
                return await evidence_files.upload_eligibility_evidence(
                    session,
                    org,
                    admin_user,
                    profile.learner_uuid,
                    UploadFile(content, filename="fixture.pdf"),
                    "fixture-upload",
                )

        async def stored_bytes(**_kwargs):
            async def chunks():
                yield b"%PDF-1.7\nSynthetic evidence"

            return chunks()

        with (
            patch.object(
                evidence_files,
                "_authorize_evidence_action",
                AsyncMock(return_value=[SimpleNamespace(id=1)]),
            ),
            patch.object(
                evidence_files,
                "_approved_file_rules",
                return_value=(1024, {"application/pdf"}, "learner_evidence_core"),
            ),
            patch.object(evidence_files, "upload_content", new_callable=AsyncMock) as store,
            patch.object(evidence_files, "open_content_stream", side_effect=stored_bytes),
            patch("src.services.courses.transfer.storage_utils.delete_storage_file") as remove,
        ):
            with (
                patch.object(db, "commit", side_effect=lose_acknowledgment),
                pytest.raises(ConnectionError, match="Commit acknowledgment lost"),
            ):
                await upload(db)
            retry = await upload(retry_db)
        stored = (await retry_db.execute(
            select(StudyEvidenceFile).where(StudyEvidenceFile.org_id == org.id)
        )).scalar_one()
        assert retry.evidence_uuid == stored.evidence_uuid
        assert retry.upload_status == "ready"
        store.assert_awaited_once()
        remove.assert_not_called()

    @staticmethod
    def _policy(key, specified=None):
        policy_key = StudyPolicyKey(key)
        specified = specified or {}
        decision_items = {
            area: StudyPolicyDecisionValue(
                state=StudyPolicyDecisionState.NOT_APPLICABLE,
                rationale="Synthetic test fixture only.",
                evidence_reference=f"TEST-{key}-{area}",
            )
            for area in POLICY_REQUIRED_DECISION_AREAS[policy_key]
        }
        for area, value in specified.items():
            decision_items[area] = StudyPolicyDecisionValue(
                state=StudyPolicyDecisionState.SPECIFIED,
                value=value,
                rationale="Synthetic test fixture only.",
                evidence_reference=f"TEST-{key}-{area}",
            )
        return SimpleNamespace(
            policy_key=policy_key.value,
            body=StudyPolicyBody(decision_items=decision_items).model_dump(mode="json"),
        )

    def test_missing_decisions_refuse_every_upload(self):
        for policies in ([], [self._policy("D-13", {})], [self._policy("D-15", {})]):
            with pytest.raises(HTTPException) as exc:
                _approved_file_rules(policies)
            assert exc.value.status_code == 409
            assert "D-13 and D-15" in exc.value.detail

    def test_unapproved_retention_category_is_not_invented(self):
        policies = [
            self._policy("D-13", {"retention": {"learner_evidence_retention_category": "Not Approved"}}),
            self._policy("D-15", {"supported_formats": {"max_bytes": 1024, "media_types": ["application/pdf"]}}),
        ]
        with pytest.raises(HTTPException) as exc:
            _approved_file_rules(policies)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "RETENTION_CATEGORY_UNCONFIGURED"

    def test_unapproved_size_and_format_are_not_silently_defaulted(self):
        for supported_formats in (
            None,
            {"max_bytes": 0, "media_types": ["application/pdf"]},
            {"max_bytes": 600 * 1024 * 1024, "media_types": ["application/pdf"]},
            {"max_bytes": 1024, "media_types": ["application/x-executable"]},
        ):
            policies = [
                self._policy("D-13", {"retention": {"learner_evidence_retention_category": "learner_evidence_core"}}),
                self._policy(
                    "D-15",
                    {"supported_formats": supported_formats} if supported_formats is not None else None,
                ),
            ]
            with pytest.raises(HTTPException) as exc:
                _approved_file_rules(policies)
            assert exc.value.status_code == 409
            assert exc.value.detail["code"] == "EVIDENCE_FILE_POLICY_UNCONFIGURED"

    def test_approved_rules_are_returned_without_deriving_anything(self):
        max_bytes, media_types, category = _approved_file_rules(
            [
                self._policy("D-13", {"retention": {"learner_evidence_retention_category": "learner_evidence_core"}}),
                self._policy(
                    "D-15",
                    {"supported_formats": {"max_bytes": 20_480_000, "media_types": ["application/pdf", "image/png"]}},
                ),
            ]
        )
        assert (max_bytes, category) == (20_480_000, "learner_evidence_core")
        assert media_types == {"application/pdf", "image/png"}

    async def test_evidence_rows_reject_unsafe_metadata(self, db, org, admin_user, regular_user):
        profile = StudyLearnerProfile(
            learner_uuid="learner_1",
            org_id=org.id,
            user_id=regular_user.id,
            created_by_user_id=admin_user.id,
        )
        db.add(profile)
        await db.commit()
        profile_id = profile.id

        base = {
            "evidence_uuid": "ev_1",
            "org_id": org.id,
            "learner_profile_id": profile_id,
            "purpose": "eligibility_evidence",
            "storage_reference": "content/orgs/org_test/studytomy/evidence/a.pdf",
            "content_sha256": "a" * 64,
            "media_type": "application/pdf",
            "size_bytes": 1024,
            "retention_category": "learner_evidence_core",
            "upload_status": "ready",
            "uploaded_by_user_id": admin_user.id,
        }
        for field, value in (
            ("size_bytes", 0),
            ("content_sha256", "too-short"),
            ("purpose", "case_evidence"),
            ("upload_status", "done"),
        ):
            db.add(StudyEvidenceFile(**{**base, field: value}))
            with pytest.raises(IntegrityError):
                await db.flush()
            await db.rollback()

        db.add(StudyEvidenceFile(**base))
        await db.flush()
        stored = (
            await db.execute(
                select(StudyEvidenceFile).where(StudyEvidenceFile.evidence_uuid == "ev_1")
            )
        ).scalars().all()
        assert len(stored) == 1
        assert stored[0].upload_status == "ready"
        assert stored[0].content_sha256 == "a" * 64


class TestOfferReadinessGates:
    """TASK-008: absent configuration blocks sale instead of falling through."""

    async def test_unconfigured_course_is_never_sale_ready(self, db, org, course):
        readiness = await evaluate_course_offer_readiness(db, org, course.course_uuid)
        assert readiness.ready_to_sell is False
        codes = {blocker.code for blocker in readiness.blockers}
        assert {
            "DECISION_NOT_APPROVED",
            "SERVICE_OWNER_MISSING",
            "COURSE_REVIEW_EVIDENCE_MISSING",
            "SUPPORT_CAPACITY_EVIDENCE_MISSING",
            "PILOT_ACCEPTANCE_EVIDENCE_MISSING",
        } <= codes
        blocked_decisions = {
            blocker.policy_key.value for blocker in readiness.blockers if blocker.policy_key
        }
        assert {
            "D-01",
            "D-02",
            "D-04",
            "D-05",
            "D-15",
            "D-17",
            "D-18",
        } <= blocked_decisions
        assert "D-06" not in blocked_decisions

    async def test_membership_offers_add_their_own_decisions(self, db, org, course):
        readiness = await evaluate_course_offer_readiness(
            db, org, course.course_uuid, StudyOfferKind.MEMBERSHIP
        )
        blocked = {
            blocker.policy_key.value for blocker in readiness.blockers if blocker.policy_key
        }
        assert {"D-05", "D-06"} <= blocked

    async def test_unpublished_content_reports_an_extra_blocker(self, db, org, course):
        course.published = False
        db.add(course)
        await db.commit()
        readiness = await evaluate_course_offer_readiness(db, org, course.course_uuid)
        assert "COURSE_NOT_PUBLISHED" in {blocker.code for blocker in readiness.blockers}

    async def test_readiness_is_computed_and_never_mutates(self, db, org, course):
        before = (await db.execute(select(Organization).where(Organization.id == org.id))).scalars().one()
        first = await evaluate_course_offer_readiness(db, org, course.course_uuid)
        second = await evaluate_course_offer_readiness(db, org, course.course_uuid)
        assert [b.code for b in first.blockers] == [b.code for b in second.blockers]
        assert first.evaluated_at <= second.evaluated_at
        assert before.slug == org.slug

    async def test_an_unknown_course_is_not_evaluated(self, db, org):
        with pytest.raises(HTTPException) as exc:
            await evaluate_course_offer_readiness(db, org, "course_absent")
        assert exc.value.status_code == 404
