"""Opt-in fixtures for tests that must exercise PostgreSQL semantics.

These fixtures never read the application database settings and never run
migrations. The operator must point ``STUDYTOMY_TEST_DATABASE_URL`` at an
already migrated, loopback-only database whose name ends in ``_test``.
"""

import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

_ROUTING_QUERY_KEYS = {"host", "hostaddr", "service"}


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    normalized = host.rstrip(".").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validated_test_url(raw_url: str) -> URL:
    """Return a PostgreSQL URL only when it cannot select a remote database."""
    try:
        url = make_url(raw_url)
    except (ArgumentError, TypeError, ValueError) as error:
        raise pytest.UsageError(
            "STUDYTOMY_TEST_DATABASE_URL must be a valid local PostgreSQL URL."
        ) from error

    if url.get_backend_name() != "postgresql":
        raise pytest.UsageError(
            "STUDYTOMY_TEST_DATABASE_URL must use PostgreSQL; SQLite cannot verify row locks."
        )
    if not _is_loopback_host(url.host):
        raise pytest.UsageError(
            "PostgreSQL concurrency tests only accept a loopback database host."
        )
    if not url.database or not url.database.lower().endswith("_test"):
        raise pytest.UsageError(
            "The PostgreSQL test database name must end in '_test'."
        )
    if _ROUTING_QUERY_KEYS.intersection(key.lower() for key in url.query):
        raise pytest.UsageError(
            "PostgreSQL test URLs cannot use host/service routing query parameters."
        )

    # All tests use SQLAlchemy's async session API; normalize accepted
    # postgresql/psycopg URLs to the already-declared asyncpg driver.
    return url.set(drivername="postgresql+asyncpg")


def _configured_test_url(environ: Mapping[str, str]) -> URL | None:
    """Read only the dedicated test variable; never fall back to runtime URLs."""
    raw_url = environ.get("STUDYTOMY_TEST_DATABASE_URL")
    return _validated_test_url(raw_url) if raw_url else None


def _check_migrated_schema(connection) -> None:
    from src.core.migrations import current_revisions, head_revisions

    current = current_revisions(connection)
    expected = head_revisions()
    if current != expected:
        raise AssertionError(
            "PostgreSQL test database must be migrated to the current Alembic "
            f"heads (current={current!r}, expected={expected!r})."
        )

    required_tables = {
        "organization",
        "user",
        "st_idempotency_record",
        "st_outbox_record",
    }
    missing = required_tables.difference(inspect(connection).get_table_names())
    if missing:
        raise AssertionError(
            "PostgreSQL test database is missing migrated Studytomy tables: "
            + ", ".join(sorted(missing))
        )

    expected_guards = {
        ("st_assessment_response_archive", "st_assessment_archive_immutable"),
        ("st_business_event", "st_business_event_immutable"),
        ("st_eligibility_decision", "st_eligibility_decision_immutable"),
        ("st_consent_event", "st_consent_event_immutable"),
        ("st_grounded_lesson_evidence", "st_grounded_lesson_evidence_immutable"),
        ("st_grounded_quiz_evidence", "st_grounded_quiz_evidence_immutable"),
        ("st_draft_material", "st_draft_material_version_guard"),
        ("st_offer", "st_offer_draft_root_protected"),
        ("st_offer_version", "st_offer_version_immutable"),
    }
    actual_guards = set(
        connection.execute(
            text(
                """
                SELECT table_class.relname, trg.tgname
                FROM pg_trigger AS trg
                JOIN pg_class AS table_class ON table_class.oid = trg.tgrelid
                WHERE NOT trg.tgisinternal
                """
            )
        ).tuples()
    )
    missing_guards = expected_guards.difference(actual_guards)
    if missing_guards:
        raise AssertionError(
            "PostgreSQL test database is missing Studytomy invariant triggers: "
            + ", ".join(
                f"{table}.{trigger}" for table, trigger in sorted(missing_guards)
            )
        )


@pytest.fixture
async def postgres_session_factory():
    """Create independent sessions against a pre-migrated local test database."""
    url = _configured_test_url(os.environ)
    if url is None:
        pytest.skip("Set STUDYTOMY_TEST_DATABASE_URL to opt into local PostgreSQL tests.")

    engine = create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=4,
        max_overflow=0,
    )
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_check_migrated_schema)
        yield async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    finally:
        await engine.dispose()


@dataclass(frozen=True)
class PostgresTestRows:
    """Unique, non-personal rows used by a single PostgreSQL test."""

    session_factory: async_sessionmaker
    org_id: int
    user_id: int
    token: str


@pytest.fixture
async def postgres_test_rows(postgres_session_factory):
    from src.db.organizations import Organization
    from src.db.studytomy.operations import (
        StudyIdempotencyRecord,
        StudyOutboxRecord,
    )
    from src.db.users import User

    token = uuid4().hex
    now = datetime.now(UTC)
    org = Organization(
        name="Studytomy PostgreSQL test",
        slug=f"st-pg-{token}",
        email=f"st-pg-{token}@example.com",
        org_uuid=f"st-pg-{token}",
        creation_date=now.isoformat(),
        update_date=now.isoformat(),
    )
    user = User(
        username=f"stpg_{token}",
        first_name="PostgreSQL",
        last_name="Test",
        email=f"st-pg-{token}@example.com",
        user_uuid=f"st-pg-{token}",
        creation_date=now.isoformat(),
        update_date=now.isoformat(),
    )

    async with postgres_session_factory() as session:
        session.add(org)
        session.add(user)
        await session.commit()
        org_id, user_id = org.id, user.id

    try:
        yield PostgresTestRows(
            session_factory=postgres_session_factory,
            org_id=org_id,
            user_id=user_id,
            token=token,
        )
    finally:
        async with postgres_session_factory() as session, session.begin():
            await session.execute(
                delete(StudyOutboxRecord).where(StudyOutboxRecord.org_id == org_id)
            )
            await session.execute(
                delete(StudyIdempotencyRecord).where(
                    StudyIdempotencyRecord.org_id == org_id
                )
            )
            await session.execute(delete(Organization).where(Organization.id == org_id))
            await session.execute(delete(User).where(User.id == user_id))
