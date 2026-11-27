"""PostgreSQL-only checks for the real schema bootstrap transaction.

These tests create uniquely named schemas inside the explicitly configured,
loopback-only ``STUDYTOMY_TEST_DATABASE_URL``. They never read the runtime
database configuration and never run against a production or development DB.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import io
import ipaddress
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
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
    if not loopback or not url.database or not url.database.lower().endswith("_test") or url.query:
        pytest.fail("Migration rehearsals require a loopback PostgreSQL database ending in _test.")
    if url.drivername not in {"postgresql", "postgresql+psycopg2"}:
        pytest.fail("Migration rehearsals require the declared synchronous PostgreSQL driver.")
    return url


@contextmanager
def _isolated_database():
    base_url = _test_database_url()
    database = f"st_aud19_{uuid4().hex}_test"
    admin_engine = create_engine(base_url)
    created = False
    try:
        with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            exists = connection.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname=:database)"),
                {"database": database},
            ).scalar_one()
            if exists:
                pytest.fail("Refusing to reuse an existing AUD-19 database.")
            connection.execute(text(f'CREATE DATABASE "{database}" TEMPLATE template0'))
            created = True

        database_url = base_url.set(database=database).render_as_string(hide_password=False)
        yield database_url, database
    finally:
        try:
            if created:
                with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                    connection.execute(text(f'DROP DATABASE "{database}"'))
        finally:
            admin_engine.dispose()


@pytest.mark.studytomy_postgres
def test_empty_schema_bootstrap_serializes_and_installs_integrity_guards():
    with _isolated_database() as (database_url, database):
        with ThreadPoolExecutor(max_workers=2) as workers:
            reports = list(workers.map(lambda _: ensure_schema(database_url), range(2)))

        expected_heads = head_revisions()
        assert expected_heads == ["2d6a1b7c9e40"]
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

                retention_columns = {
                    column["name"] for column in inspect(connection).get_columns("st_learning_surface")
                }
                assert {"retention_policy_id", "retention_category", "retention_expires_at"} <= retention_columns

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
        current_head = initial.after[0]
        immediate_parent = migrations.ScriptDirectory.from_config(
            migrations.alembic_config()
        ).get_revision(current_head).down_revision
        assert immediate_parent == "f04d9c6a31b8"

        engine = create_engine(database_url)
        try:
            with engine.begin() as connection:
                command.downgrade(migrations.alembic_config(connection), immediate_parent)
                assert migrations.current_revisions(connection) == [immediate_parent]
        finally:
            engine.dispose()

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
                assert migrations.current_revisions(connection) == [immediate_parent]
                probe = connection.execute(
                    text("SELECT to_regclass(:qualified_name)"),
                    {"qualified_name": "aud19_interrupted_migration_probe"},
                ).scalar_one()
                assert probe is None, "DDL from the interrupted migration was not rolled back."
                columns = {column["name"] for column in inspect(connection).get_columns("st_learning_surface")}
                assert "retention_policy_id" not in columns
        finally:
            engine.dispose()


@pytest.mark.studytomy_postgres
def test_supported_prior_revision_upgrades_through_application_bootstrap():
    with _isolated_database() as (database_url, database):
        initial = ensure_schema(database_url)
        current_head = head_revisions()[0]
        assert initial.after == [current_head]

        head = migrations.ScriptDirectory.from_config(migrations.alembic_config()).get_revision(current_head)
        immediate_parent = head.down_revision
        assert immediate_parent == "f04d9c6a31b8"

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
                command.downgrade(migrations.alembic_config(connection), immediate_parent)
                assert migrations.current_revisions(connection) == [immediate_parent]
                for table in ("st_learning_surface", "st_learning_action"):
                    columns = {column["name"] for column in inspect(connection).get_columns(table)}
                    assert not {"retention_policy_id", "retention_category", "retention_expires_at"} & columns
                assert connection.execute(
                    text("SELECT count(*) FROM st_business_event WHERE event_uuid=:id"),
                    {"id": event_uuid},
                ).scalar_one() == 1

            upgraded = ensure_schema(database_url)
            assert upgraded.before == [immediate_parent]
            assert upgraded.after == [current_head]
            with engine.connect() as connection:
                for table in ("st_learning_surface", "st_learning_action"):
                    columns = {column["name"] for column in inspect(connection).get_columns(table)}
                    assert {"retention_policy_id", "retention_category", "retention_expires_at"} <= columns
                assert connection.execute(
                    text("SELECT count(*) FROM st_business_event WHERE event_uuid=:id"),
                    {"id": event_uuid},
                ).scalar_one() == 1

            # Keep the older syllabus-revocation downgrade check independent from
            # the current retention migration round trip above.
            historical_parent = migrations.ScriptDirectory.from_config(
                migrations.alembic_config()
            ).get_revision(immediate_parent).down_revision
            assert historical_parent == "e3c4b1d928af"
            with engine.begin() as connection:
                command.downgrade(migrations.alembic_config(connection), historical_parent)
                assert migrations.current_revisions(connection) == [historical_parent]
                inspector = inspect(connection)
                for table in ("st_syllabus_source_version", "st_syllabus_concept_mapping"):
                    columns = {column["name"] for column in inspector.get_columns(table)}
                    assert "revocation_evidence" not in columns
                assert connection.execute(
                    text("SELECT count(*) FROM st_business_event WHERE event_uuid=:id"),
                    {"id": event_uuid},
                ).scalar_one() == 1

            historical_upgrade = ensure_schema(database_url)
            assert historical_upgrade.before == [historical_parent]
            assert historical_upgrade.after == [current_head]
            with engine.connect() as connection:
                inspector = inspect(connection)
                for table in ("st_syllabus_source_version", "st_syllabus_concept_mapping"):
                    columns = {column["name"] for column in inspector.get_columns(table)}
                    assert {"revoked_by_user_id", "revocation_evidence", "revoked_at"} <= columns
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
                assert migrations.current_revisions(connection) == head_revisions()
                kept = connection.execute(
                    text("SELECT count(*) FROM st_curriculum_revision WHERE programme_metadata IS NOT NULL")
                ).scalar_one()
                assert kept == 1
        finally:
            engine.dispose()


@pytest.mark.studytomy_postgres
def test_populated_pre_studytomy_upgrade_and_restore(tmp_path):
    _test_database_url()
    tools_container = os.environ.get("STUDYTOMY_TEST_PG_TOOLS_CONTAINER")
    if not tools_container and not (shutil.which("pg_dump") and shutil.which("pg_restore")):
        pytest.skip("Add PostgreSQL pg_dump and pg_restore matching the server version to PATH.")

    # The initial migration assumes an existing native schema. Use the actual
    # historical application bootstrap rather than stamping today's models.
    baseline = "4f3346af99b516bab2d422217905d078096d55ac"
    repo = Path(__file__).resolve().parents[5]
    archive = subprocess.run(
        ["git", "archive", baseline, "apps/api"], cwd=repo, capture_output=True,
    )
    if archive.returncode:
        pytest.skip(f"Fetch historical Studytomy bootstrap commit {baseline} for this rehearsal.")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as snapshot:
        snapshot.extractall(tmp_path, filter="data")

    with (
        _isolated_database() as (source_url, source_db),
        _isolated_database() as (restore_url, restore_db),
    ):
        historical_api = tmp_path / "apps/api"
        fixture = subprocess.run(
            [sys.executable, "-c", """
import os
from sqlalchemy import create_engine
from sqlmodel import Session
from src.core.migrations import ensure_schema
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.users import User
from src.db.trails import Trail
from src.db.trail_runs import StatusEnum, TrailRun

url = os.environ["STUDYTOMY_TEST_DATABASE_URL"]
assert ensure_schema(url).after == ["f5a8b3d0c2e4"]
engine = create_engine(url)
with Session(engine) as session:
    org = Organization(name="Synthetic school", slug="audit-school", org_uuid="org_audit", email="school@example.invalid")
    user = User(username="audit-learner", first_name="Synthetic", last_name="Learner",
                email="learner@example.invalid", user_uuid="user_audit")
    session.add_all([org, user])
    session.flush()
    course = Course(name="Synthetic physics", org_id=org.id, course_uuid="course_audit",
                    public=True, published=True, open_to_contributors=False)
    trail = Trail(org_id=org.id, user_id=user.id, trail_uuid="trail_audit")
    session.add_all([course, trail])
    session.flush()
    session.add(TrailRun(trail_id=trail.id, course_id=course.id, org_id=org.id, user_id=user.id,
                         status=StatusEnum.STATUS_COMPLETED, creation_date="2026-01-01", update_date="2026-01-01"))
    session.commit()
engine.dispose()
"""],
            env={**os.environ, "STUDYTOMY_TEST_DATABASE_URL": source_url, "PYTHONPATH": str(historical_api)},
            cwd=historical_api, capture_output=True, text=True,
        )
        assert fixture.returncode == 0, "Historical application failed to provision the synthetic fixture."

        connection = make_url(source_url)
        pg_env = {
            **{key: value for key, value in os.environ.items() if not key.startswith("PG")},
            "PGHOST": connection.host,
            "PGPORT": str(connection.port or 5432),
            "PGUSER": connection.username,
            "PGPASSWORD": connection.password or "",
        }
        prefix = []
        if tools_container:
            # Use client binaries from the same disposable server image in CI.
            pg_env.update(PGHOST="127.0.0.1", PGPORT="5432")
            prefix = [
                "docker", "exec", "-i", "-e", "PGHOST", "-e", "PGPORT",
                "-e", "PGUSER", "-e", "PGPASSWORD", tools_container,
            ]
        backup = tmp_path / "pre-studytomy.dump"
        with backup.open("wb") as output:
            subprocess.run(
                [*prefix, "pg_dump", "--no-password", "--format=custom", source_db],
                env=pg_env, check=True, stdout=output, stderr=subprocess.PIPE,
            )
        with backup.open("rb") as source:
            subprocess.run(
                [*prefix, "pg_restore", "--no-password", "--exit-on-error", "--single-transaction",
                 "--dbname", restore_db],
                env=pg_env, check=True, stdin=source, capture_output=True,
            )

        expected_rows = [("org_audit", "user_audit", "course_audit", True, True, "trail_audit", "STATUS_COMPLETED")]
        current_heads = head_revisions()
        assert len(current_heads) == 1
        for url in (source_url, restore_url):
            engine = create_engine(url)
            try:
                for expected_head in (["f5a8b3d0c2e4"], current_heads):
                    with engine.connect() as reader:
                        assert migrations.current_revisions(reader) == expected_head
                        assert reader.execute(text("""
                            SELECT o.org_uuid, u.user_uuid, c.course_uuid, c.public, c.published, t.trail_uuid, e.status
                            FROM trailrun e
                            JOIN organization o ON o.id = e.org_id
                            JOIN "user" u ON u.id = e.user_id
                            JOIN course c ON c.id = e.course_id AND c.org_id = o.id
                            JOIN trail t ON t.id = e.trail_id AND t.user_id = u.id AND t.org_id = o.id
                        """)).all() == expected_rows
                        if expected_head == current_heads:
                            assert reader.execute(text(
                                "SELECT studytomy_managed FROM course WHERE course_uuid = 'course_audit'"
                            )).scalar_one() is False
                            assert "ck_st_course_release_digest" in {
                                c["name"] for c in inspect(reader).get_check_constraints("st_course_release")
                            }
                    if expected_head != current_heads:
                        assert ensure_schema(url).after == current_heads
            finally:
                engine.dispose()
