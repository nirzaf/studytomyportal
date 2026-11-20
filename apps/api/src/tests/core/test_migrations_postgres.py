"""PostgreSQL-only checks for the real schema bootstrap transaction.

These tests create uniquely named schemas inside the explicitly configured,
loopback-only ``STUDYTOMY_TEST_DATABASE_URL``. They never read the runtime
database configuration and never run against a production or development DB.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import ipaddress
import os
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session

from src.core import migrations
from src.core.migrations import SchemaMigrationError, ensure_schema, head_revisions
from src.db.organizations import Organization
from src.db.studytomy.governance import StudyBusinessEvent


def _test_database_url():
    raw = os.environ.get("STUDYTOMY_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set STUDYTOMY_TEST_DATABASE_URL to opt into local PostgreSQL tests.")

    url = make_url(migrations.sync_database_url(raw))
    if not url.host:
        pytest.fail("Migration rehearsals require an explicitly configured loopback host.")
    try:
        loopback = ipaddress.ip_address(url.host).is_loopback
    except ValueError:
        loopback = url.host == "localhost"
    if not loopback or not url.database or not url.database.lower().endswith("_test"):
        pytest.fail("Migration rehearsals require a loopback PostgreSQL database ending in _test.")
    if url.drivername not in {"postgresql", "postgresql+psycopg2"}:
        pytest.fail("Migration rehearsals require the declared synchronous PostgreSQL driver.")
    return url


@contextmanager
def _isolated_database():
    base_url = _test_database_url()
    database = f"st_aud19_{uuid4().hex}_test"
    admin_engine = create_engine(base_url)
    try:
        with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            exists = connection.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname=:database)"),
                {"database": database},
            ).scalar_one()
            if exists:
                pytest.fail("Refusing to reuse an existing AUD-19 database.")
            connection.execute(text(f'CREATE DATABASE "{database}" TEMPLATE template0'))

        database_url = base_url.set(database=database).render_as_string(hide_password=False)
        yield database_url, database
    finally:
        with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))
        admin_engine.dispose()


@pytest.mark.studytomy_postgres
def test_empty_schema_bootstrap_serializes_and_installs_integrity_guards():
    with _isolated_database() as (database_url, database):
        with ThreadPoolExecutor(max_workers=2) as workers:
            reports = list(workers.map(lambda _: ensure_schema(database_url), range(2)))

        expected_heads = head_revisions()
        assert expected_heads == ["b7c2e9a4d1f0"]
        assert all(report.after == expected_heads for report in reports)
        assert {report.state for report in reports} == {"empty", "stamped"}

        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                tables = set(connection.execute(text(
                    "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                )).scalars())
                assert {
                    "st_course_release",
                    "st_business_event",
                    "st_grounded_lesson_evidence",
                    "st_evidence_file",
                } <= tables

                guards = set(connection.execute(text(
                    """
                    SELECT table_class.relname, trg.tgname
                    FROM pg_trigger AS trg
                    JOIN pg_class AS table_class ON table_class.oid = trg.tgrelid
                    JOIN pg_namespace AS namespace ON namespace.oid = table_class.relnamespace
                    WHERE namespace.nspname = 'public' AND NOT trg.tgisinternal
                    """
                )).tuples())
                assert {
                    ("st_course_release", "st_course_release_reference_guard"),
                    ("st_course_release", "st_course_release_immutable"),
                    ("st_business_event", "st_business_event_immutable"),
                    ("st_grounded_lesson_evidence", "st_grounded_lesson_evidence_immutable"),
                    ("st_evidence_file", "trg_st_record_request_evidence"),
                } <= guards

                constraints = set(connection.execute(text(
                    """
                    SELECT conname FROM pg_constraint
                    WHERE connamespace = to_regnamespace('public')
                    """
                )).scalars())
                assert {
                    "ck_st_course_release_digest",
                    "ck_st_evidence_file_sha256_length",
                } <= constraints

                extension_version = connection.execute(text(
                    "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
                )).scalar_one_or_none()
                assert extension_version, "The isolated PostgreSQL database must have pgvector installed."

            token = uuid4().hex
            with Session(engine) as session:
                organization = Organization(
                    name="AUD-19 synthetic restore probe",
                    slug=f"aud19-{token}",
                    org_uuid=f"org_{token}",
                    email=f"{token}@example.invalid",
                )
                session.add(organization)
                session.flush()
                event = StudyBusinessEvent(
                    org_id=organization.id,
                    action="audit.synthetic.restore_probe",
                    resource_kind="synthetic_evidence",
                    resource_uuid=f"aud19_{token}",
                    after_state={"content_sha256": "a" * 64},
                )
                session.add(event)
                session.commit()
                event_uuid = event.event_uuid

            with engine.connect() as connection:
                transaction = connection.begin()
                savepoint = connection.begin_nested()
                try:
                    connection.execute(
                        text(
                            "UPDATE st_business_event SET action = 'tampered' "
                            "WHERE event_uuid = :event_uuid"
                        ),
                        {"event_uuid": event_uuid},
                    )
                except DBAPIError as exc:
                    savepoint.rollback()
                    assert "immutable" in str(exc.orig).lower()
                else:
                    savepoint.rollback()
                    pytest.fail("The PostgreSQL immutability trigger allowed a business-event update.")
                transaction.commit()
        finally:
            engine.dispose()


@pytest.mark.studytomy_postgres
def test_interrupted_upgrade_rolls_back_ddl_and_preserves_revision():
    with _isolated_database() as (database_url, database):
        initial = ensure_schema(database_url)
        assert initial.after == head_revisions()

        def interrupted_upgrade(config, target):
            connection = config.attributes["connection"]
            connection.execute(text("CREATE TABLE aud19_interrupted_migration_probe (id INTEGER PRIMARY KEY)"))
            raise RuntimeError("synthetic migration interruption")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(migrations.command, "upgrade", interrupted_upgrade)
            with pytest.raises(SchemaMigrationError, match="synthetic migration interruption"):
                ensure_schema(database_url)

        engine = create_engine(database_url)
        try:
            with engine.connect() as connection:
                assert migrations.current_revisions(connection) == head_revisions()
                probe = connection.execute(
                    text("SELECT to_regclass(:qualified_name)"),
                    {"qualified_name": "aud19_interrupted_migration_probe"},
                ).scalar_one()
                assert probe is None, "DDL from the interrupted migration was not rolled back."
        finally:
            engine.dispose()


@pytest.mark.studytomy_postgres
def test_supported_prior_revision_upgrades_through_application_bootstrap():
    with _isolated_database() as (database_url, database):
        initial = ensure_schema(database_url)
        assert initial.after == ["b7c2e9a4d1f0"]

        head = migrations.ScriptDirectory.from_config(migrations.alembic_config()).get_revision(
            initial.after[0]
        )
        assert head.down_revision == "e7f2c9a4b138"

        token = uuid4().hex
        engine = create_engine(database_url)
        try:
            with Session(engine, expire_on_commit=False) as session:
                organization = Organization(
                    name="AUD-19 prior-revision fixture",
                    slug=f"aud19-{token}",
                    org_uuid=f"org_{token}",
                    email=f"{token}@example.invalid",
                )
                session.add(organization)
                session.flush()
                event = StudyBusinessEvent(
                    org_id=organization.id,
                    action="audit.synthetic.prior_revision",
                    resource_kind="synthetic_evidence",
                    resource_uuid=f"aud19_{token}",
                    after_state={"content_sha256": "b" * 64},
                )
                session.add(event)
                session.commit()
                event_uuid = event.event_uuid

            with engine.begin() as connection:
                assert connection.execute(
                    text("SELECT count(*) FROM st_learning_attempt")
                ).scalar_one() == 0
                assert connection.execute(
                    text("SELECT count(*) FROM st_tutor_session")
                ).scalar_one() == 0
                command.downgrade(
                    migrations.alembic_config(connection), head.down_revision
                )
                assert migrations.current_revisions(connection) == [head.down_revision]
                playground_columns = {
                    column["name"]
                    for column in inspect(connection).get_columns("playground")
                }
                assert "thumbnail_content_sha256" not in playground_columns
                assert connection.execute(
                    text("SELECT count(*) FROM st_business_event WHERE event_uuid=:id"),
                    {"id": event_uuid},
                ).scalar_one() == 1

            # The downgrade above constructs only the exact empty parent state
            # inside this synthetic schema. The application startup contract is
            # the path under test: it upgrades the prior revision through ensure_schema().
            upgraded = ensure_schema(database_url)
            assert upgraded.before == [head.down_revision]
            assert upgraded.after == ["b7c2e9a4d1f0"]

            with engine.connect() as connection:
                playground_columns = {
                    column["name"]
                    for column in inspect(connection).get_columns("playground")
                }
                assert "thumbnail_content_sha256" in playground_columns
                assert connection.execute(
                    text("SELECT count(*) FROM st_business_event WHERE event_uuid=:id"),
                    {"id": event_uuid},
                ).scalar_one() == 1
        finally:
            engine.dispose()


@pytest.mark.studytomy_postgres
def test_lossy_downgrade_of_dependent_and_provenance_revision_is_refused():
    """T-18: Downgrade past e7f2c9a4b138 refuses to drop populated provenance/dependent data."""
    with _isolated_database() as (database_url, _database):
        ensure_schema(database_url)
        token = uuid4().hex
        engine = create_engine(database_url)
        try:
            with Session(engine, expire_on_commit=False) as session:
                organization = Organization(
                    name="T18 Guard Org",
                    slug=f"t18-{token}",
                    org_uuid=f"org_{token}",
                    email=f"{token}@example.invalid",
                )
                session.add(organization)
                session.commit()
                org_id = organization.id
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO st_curriculum_revision "
                        "(uuid, org_id, owner_course_uuid, curriculum_key, revision, subject, level, language, source_reference, rights_reference, programme_metadata, policy_version_ids, status, version, created_at, updated_at) "
                        "VALUES (:uuid, :org_id, :course_uuid, 'LK-PHY-G12', 1, 'Physics', 'Grade 12', 'en', 'NIE-REF', 'RIGHTS-REF', :meta, '[]'::json, 'draft', 1, now(), now())"
                    ),
                    {
                        "uuid": f"curr_{token}",
                        "org_id": org_id,
                        "course_uuid": f"course_{token}",
                        "meta": '{"authority":"NIE Sri Lanka","syllabus_edition":"2017","effective_cohort":"2026"}',
                    },
                )

            with pytest.raises(RuntimeError, match="Refusing lossy downgrade of revision e7f2c9a4b138"):
                with engine.begin() as connection:
                    command.downgrade(migrations.alembic_config(connection), "c4e8a1b7d209")

            # Verify transaction rolled back cleanly and revision + data remain intact
            with engine.connect() as connection:
                assert migrations.current_revisions(connection) == ["b7c2e9a4d1f0"]
                kept = connection.execute(
                    text("SELECT count(*) FROM st_curriculum_revision WHERE programme_metadata IS NOT NULL")
                ).scalar_one()
                assert kept == 1
        finally:
            engine.dispose()
