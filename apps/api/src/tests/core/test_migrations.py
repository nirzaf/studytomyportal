"""The schema step every deploy path runs (src/core/migrations.py).

Branch logic runs against SQLite files with small stand-in models; the
Postgres-only parts (advisory lock, pgvector) use fakes. The real upgrade
path against Postgres is exercised by the api-migrations CI job.
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import (
    JSON,
    Column,
    Enum,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    inspect,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError

from src.core import migrations
from src.core.migrations import (
    SchemaMigrationError,
    SchemaReport,
    auto_migrate_enabled,
    current_revisions,
    ensure_schema,
    head_revisions,
    load_models,
    reconcile,
    sync_database_url,
)


def _models(with_extra_column: bool = True) -> MetaData:
    md = MetaData()
    columns = [Column("id", Integer, primary_key=True), Column("name", String, nullable=False)]
    if with_extra_column:
        columns.append(Column("level", String, nullable=False))
    Table("widget", md, *columns)
    Table("gadget", md, Column("id", Integer, primary_key=True), Column("widget_id", Integer), Index("ix_gadget_widget", "widget_id"))
    return md


@pytest.fixture
def db_url(tmp_path):
    return f"sqlite:///{tmp_path / 'schema.db'}"


def _tables(url):
    engine = create_engine(url)
    try:
        insp = inspect(engine)
        return {t: {c["name"]: c for c in insp.get_columns(t)} for t in insp.get_table_names()}
    finally:
        engine.dispose()


def _revisions(url):
    engine = create_engine(url)
    try:
        with engine.connect() as c:
            return current_revisions(c)
    finally:
        engine.dispose()


class TestEnsureSchema:
    def test_empty_database_is_created_and_stamped(self, db_url):
        report = ensure_schema(db_url, metadata=_models())
        assert report.state == "empty"
        assert report.before == []
        assert report.after == head_revisions()
        assert {"widget", "gadget", "alembic_version"} <= set(_tables(db_url))

    def test_empty_studytomy_schema_installs_non_model_database_guards(self, db_url):
        metadata = MetaData()
        event_table = Table(
            "st_business_event",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("action", String),
        )
        decision_table = Table(
            "st_eligibility_decision",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("outcome", String),
        )
        consent_table = Table(
            "st_consent_event",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("choice", String),
        )
        offer_table = Table(
            "st_offer",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("offer_uuid", String),
            Column("org_id", Integer),
            Column("offer_kind", String),
            Column("status", String),
            Column("created_by_user_id", Integer),
            Column("created_at", String),
            Column("current_version_no", Integer),
        )
        offer_version_table = Table(
            "st_offer_version",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("offer_id", Integer),
            Column("version_no", Integer),
            Column("content_sha256", String),
        )
        grounded_lesson_table = Table(
            "st_grounded_lesson_evidence",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("content_sha256", String),
            Column("citations", JSON),
        )
        grounded_quiz_table = Table(
            "st_grounded_quiz_evidence",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("content_sha256", String),
            Column("citations", JSON),
        )

        report = ensure_schema(db_url, metadata=metadata)
        assert report.state == "empty"
        engine = create_engine(db_url)
        try:
            with engine.begin() as connection:
                trigger_names = set(
                    connection.execute(
                        text("SELECT name FROM sqlite_master WHERE type='trigger'")
                    ).scalars()
                )
                assert {
                    "st_business_event_immutable_update",
                    "st_business_event_immutable_delete",
                    "st_eligibility_decision_immutable_update",
                    "st_eligibility_decision_immutable_delete",
                    "st_consent_event_immutable_update",
                    "st_consent_event_immutable_delete",
                    "st_offer_draft_root_protected_update",
                    "st_offer_draft_root_protected_delete",
                    "st_offer_version_immutable_update",
                    "st_offer_version_immutable_delete",
                    "st_grounded_lesson_evidence_immutable_update",
                    "st_grounded_lesson_evidence_immutable_delete",
                    "st_grounded_quiz_evidence_immutable_update",
                    "st_grounded_quiz_evidence_immutable_delete",
                } <= trigger_names

                connection.execute(event_table.insert().values(id=1, action="test.action"))
                connection.execute(decision_table.insert().values(id=1, outcome="eligible"))
                connection.execute(consent_table.insert().values(id=1, choice="accept"))
                connection.execute(
                    offer_table.insert().values(
                        id=1,
                        offer_uuid="test-offer",
                        org_id=1,
                        offer_kind="course",
                        status="draft",
                        created_by_user_id=1,
                        created_at="2026-01-01T00:00:00",
                        current_version_no=1,
                    )
                )
                connection.execute(
                    offer_version_table.insert().values(
                        id=1,
                        offer_id=1,
                        version_no=1,
                        content_sha256="a" * 64,
                    )
                )
                connection.execute(
                    grounded_lesson_table.insert().values(
                        id=1, content_sha256="a" * 64, citations=[]
                    )
                )
                connection.execute(
                    grounded_quiz_table.insert().values(
                        id=1, content_sha256="a" * 64, citations={}
                    )
                )

                immutable_rows = (
                    (event_table, "action", "changed", "st_business_event rows are immutable"),
                    (decision_table, "outcome", "ineligible", "st_eligibility_decision rows are immutable"),
                    (consent_table, "choice", "decline", "st_consent_event rows are immutable"),
                    (offer_version_table, "content_sha256", "b" * 64, "st_offer_version rows are immutable"),
                    (
                        grounded_lesson_table,
                        "content_sha256",
                        "b" * 64,
                        "Grounded source evidence is immutable",
                    ),
                    (
                        grounded_quiz_table,
                        "content_sha256",
                        "b" * 64,
                        "Grounded source evidence is immutable",
                    ),
                )
                for table, column, value, error_message in immutable_rows:
                    for mutation in ("update", "delete"):
                        statement = (
                            update(table).where(table.c.id == 1).values({column: value})
                            if mutation == "update"
                            else delete(table).where(table.c.id == 1)
                        )
                        with pytest.raises(IntegrityError, match=error_message):
                            connection.execute(statement)

                with pytest.raises(IntegrityError, match="st_offer roots are draft-only"):
                    connection.execute(
                        update(offer_table)
                        .where(offer_table.c.id == 1)
                        .values(status="published")
                    )
                with pytest.raises(IntegrityError, match="st_offer draft roots are retained"):
                    connection.execute(delete(offer_table).where(offer_table.c.id == 1))
        finally:
            engine.dispose()

    def test_unstamped_legacy_studytomy_schema_gets_missing_guards(self, db_url):
        metadata = MetaData()
        event_table = Table(
            "st_business_event",
            metadata,
            Column("id", Integer, primary_key=True),
            Column("action", String),
        )
        engine = create_engine(db_url)
        try:
            metadata.create_all(engine)
        finally:
            engine.dispose()

        report = ensure_schema(db_url, metadata=metadata)
        assert report.state == "unstamped"

        engine = create_engine(db_url)
        try:
            with engine.begin() as connection:
                trigger_names = set(
                    connection.execute(
                        text("SELECT name FROM sqlite_master WHERE type='trigger'")
                    ).scalars()
                )
                assert {
                    "st_business_event_immutable_update",
                    "st_business_event_immutable_delete",
                } <= trigger_names

                connection.execute(event_table.insert().values(id=1, action="test.action"))
                with pytest.raises(
                    IntegrityError, match="st_business_event rows are immutable"
                ):
                    connection.execute(
                        update(event_table)
                        .where(event_table.c.id == 1)
                        .values(action="changed")
                    )
        finally:
            engine.dispose()

    def test_unstamped_database_is_reconciled_then_stamped(self, db_url, caplog):
        ensure_schema(db_url, metadata=_models())
        engine = create_engine(db_url)
        with engine.begin() as c:
            c.execute(text("DROP TABLE alembic_version"))
            c.execute(text("DROP TABLE gadget"))
            c.execute(text("ALTER TABLE widget DROP COLUMN level"))
        engine.dispose()

        with caplog.at_level(logging.INFO, logger="src.core.migrations"):
            report = ensure_schema(db_url, metadata=_models())
        assert report.state == "unstamped"
        assert set(report.reconciled) == {"table gadget", "column widget.level"}
        tables = _tables(db_url)
        # NOT NULL without a default can't be added to existing rows.
        assert tables["widget"]["level"]["nullable"] is True
        engine = create_engine(db_url)
        assert [i["name"] for i in inspect(engine).get_indexes("gadget")] == ["ix_gadget_widget"]
        engine.dispose()
        assert _revisions(db_url) == head_revisions()
        assert "Brought the schema up to the models" in caplog.text

    def test_stamped_database_upgrades_and_backfills_forgotten_columns(self, db_url, caplog):
        ensure_schema(db_url, metadata=_models(with_extra_column=False))
        with patch.object(migrations.command, "upgrade") as upgrade, caplog.at_level(logging.WARNING, logger="src.core.migrations"):
            report = ensure_schema(db_url, metadata=_models())
        upgrade.assert_called_once()
        assert upgrade.call_args.args[1] == "heads"
        assert report.state == "stamped"
        assert report.reconciled == ["column widget.level"]
        assert "add a migration" in caplog.text

    def test_up_to_date_database_is_left_alone(self, db_url):
        ensure_schema(db_url, metadata=_models())
        with patch.object(migrations.command, "upgrade"):
            report = ensure_schema(db_url, metadata=_models())
        assert report.state == "stamped" and report.reconciled == [] and report.skipped == []

    def test_failed_upgrade_raises_and_rolls_back(self, db_url):
        ensure_schema(db_url, metadata=_models(with_extra_column=False))
        with patch.object(migrations.command, "upgrade", side_effect=RuntimeError("bad migration")):
            with pytest.raises(SchemaMigrationError, match="bad migration"):
                ensure_schema(db_url, metadata=_models())
        assert "level" not in _tables(db_url)["widget"]

    def test_wrong_revision_after_upgrade_raises(self, db_url):
        ensure_schema(db_url, metadata=_models())
        with patch.object(migrations.command, "upgrade"), patch.object(migrations, "head_revisions", return_value=["zzz"]):
            with pytest.raises(SchemaMigrationError, match="expected"):
                ensure_schema(db_url, metadata=_models())

    def test_unknown_revision_fails_closed_by_default(self, db_url, monkeypatch):
        ensure_schema(db_url, metadata=_models())
        unknown_revision = "f6e7d8c9b0a1"
        engine = create_engine(db_url)
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num=:revision"),
                {"revision": unknown_revision},
            )
        engine.dispose()
        monkeypatch.delenv("LEARNHOUSE_ALLOW_UNKNOWN_ALEMBIC_HEAD", raising=False)

        with pytest.raises(SchemaMigrationError, match="does not know"):
            ensure_schema(db_url, metadata=_models())
        assert _revisions(db_url) == [unknown_revision]

    def test_forward_compatible_mode_preserves_future_schema_and_revision(
        self, db_url, monkeypatch, caplog
    ):
        ensure_schema(db_url, metadata=_models())
        unknown_revision = "f6e7d8c9b0a1"
        engine = create_engine(db_url)
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE alembic_version SET version_num=:revision"),
                {"revision": unknown_revision},
            )
            connection.execute(
                text("CREATE TABLE future_study_table (id INTEGER PRIMARY KEY, note TEXT NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO future_study_table (id, note) VALUES (1, 'retained')")
            )
        engine.dispose()
        monkeypatch.setenv("LEARNHOUSE_ALLOW_UNKNOWN_ALEMBIC_HEAD", "true")

        with patch.object(migrations.command, "upgrade") as upgrade:
            report = ensure_schema(db_url, metadata=_models())
        upgrade.assert_not_called()
        assert report.state == "forward-compatible"
        assert report.before == report.after == [unknown_revision]
        assert _revisions(db_url) == [unknown_revision]
        engine = create_engine(db_url)
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT note FROM future_study_table WHERE id=1")
            ).scalar_one() == "retained"
        engine.dispose()
        assert "migrations are skipped" in caplog.text

    def test_forward_compatible_mode_rejects_mixed_heads(self, db_url, monkeypatch):
        ensure_schema(db_url, metadata=_models())
        known_revision = _revisions(db_url)[0]
        unknown_revision = "f6e7d8c9b0a1"
        engine = create_engine(db_url)
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": unknown_revision},
            )
        engine.dispose()
        monkeypatch.setenv("LEARNHOUSE_ALLOW_UNKNOWN_ALEMBIC_HEAD", "true")

        with pytest.raises(SchemaMigrationError, match="mixture of known and unknown"):
            ensure_schema(db_url, metadata=_models())
        assert set(_revisions(db_url)) == {known_revision, unknown_revision}

    def test_risky_differences_are_reported_not_applied(self, db_url, caplog):
        ensure_schema(db_url, metadata=_models())
        changed = MetaData()
        Table("widget", changed, Column("id", Integer, primary_key=True), Column("name", Integer, nullable=False), Column("level", String))
        Table("gadget", changed, Column("id", Integer, primary_key=True), Column("widget_id", Integer), Index("ix_gadget_widget", "widget_id"))
        with patch.object(migrations.command, "upgrade"), caplog.at_level(logging.INFO, logger="src.core.migrations"):
            report = ensure_schema(db_url, metadata=changed)
        assert report.skipped and report.reconciled == []
        assert "left alone" in caplog.text

    def test_default_metadata_and_url(self, db_url):
        with patch.object(migrations, "load_models", return_value=_models()) as load, \
             patch.object(migrations, "sync_database_url", return_value=db_url):
            report = ensure_schema()
        assert load.called  # env.py loads the models too
        assert report.state == "empty"


class TestReconcile:
    def test_enum_columns_and_indexes(self, db_url):
        md = MetaData()
        Table("thing", md, Column("id", Integer, primary_key=True))
        engine = create_engine(db_url)
        with engine.begin() as c:
            md.create_all(c)
        target = MetaData()
        Table(
            "thing", target, Column("id", Integer, primary_key=True),
            Column("kind", Enum("a", "b", name="thing_kind"), server_default="a", nullable=False),
            Column("ref", Integer), Index("ix_thing_ref", "ref"),
        )
        with engine.begin() as c:
            applied, skipped = reconcile(c, target)
            assert set(applied) == {"column thing.kind", "column thing.ref", "index ix_thing_ref"}
            assert reconcile(c, target) == ([], [])
        with engine.begin() as c:
            applied, _ = reconcile(c, target, indexes=False)
        engine.dispose()

    def test_drop_kinds_are_ignored(self, db_url):
        engine = create_engine(db_url)
        with engine.begin() as c, patch.object(migrations, "compare_metadata", return_value=[("drop_enum", "public", "x", ("A",)), [("modify_type", None)]]):
            applied, skipped = reconcile(c, MetaData())
        engine.dispose()
        assert applied == [] and len(skipped) == 1

    def test_extra_database_objects_are_ignored(self, db_url):
        engine = create_engine(db_url)
        with engine.begin() as c:
            c.execute(text("CREATE TABLE leftover (id INTEGER PRIMARY KEY)"))
            assert reconcile(c, MetaData()) == ([], [])
        engine.dispose()


class TestHelpers:
    @pytest.mark.parametrize("value,expected", [(None, True), ("true", True), ("1", True), ("false", False), ("0", False), ("No", False), (" off ", False)])
    def test_auto_migrate_flag(self, monkeypatch, value, expected):
        if value is None:
            monkeypatch.delenv("LEARNHOUSE_AUTO_MIGRATE", raising=False)
        else:
            monkeypatch.setenv("LEARNHOUSE_AUTO_MIGRATE", value)
        assert auto_migrate_enabled() is expected

    @pytest.mark.parametrize(
        "value,expected",
        [(None, False), ("true", True), ("1", True), ("yes", True), ("on", True), ("false", False), ("0", False)],
    )
    def test_unknown_revision_rollback_flag(self, monkeypatch, value, expected):
        if value is None:
            monkeypatch.delenv("LEARNHOUSE_ALLOW_UNKNOWN_ALEMBIC_HEAD", raising=False)
        else:
            monkeypatch.setenv("LEARNHOUSE_ALLOW_UNKNOWN_ALEMBIC_HEAD", value)
        assert migrations.allow_unknown_alembic_head() is expected

    @pytest.mark.parametrize("url,expected", [
        ("postgresql+asyncpg://u:p@h/db", "postgresql://u:p@h/db"),
        ("postgresql+psycopg2://u:p@h/db", "postgresql://u:p@h/db"),
        ("postgres://u:p@h/db", "postgresql://u:p@h/db"),
        ("postgresql://u:p@h/db", "postgresql://u:p@h/db"),
        ("sqlite:///x.db", "sqlite:///x.db"),
    ])
    def test_sync_database_url(self, url, expected):
        assert sync_database_url(url) == expected

    def test_sync_database_url_from_config(self):
        cfg = MagicMock()
        cfg.database_config.sql_connection_string = "postgresql+asyncpg://u:p@h/db"
        with patch("config.config.get_learnhouse_config", return_value=cfg):
            assert sync_database_url() == "postgresql://u:p@h/db"

    def test_load_models_registers_core_tables_and_logs_failures(self, caplog):
        real = migrations.importlib.import_module

        def flaky(name, *a, **k):
            if name == "src.db.courses.drip":
                raise ImportError("boom")
            return real(name, *a, **k)

        with patch.object(migrations.importlib, "import_module", side_effect=flaky), caplog.at_level(logging.ERROR):
            md = load_models()
        assert "course" in md.tables and "drip_notification" in md.tables
        assert "src.db.courses.drip" in caplog.text

    def test_summary(self):
        assert SchemaReport(state="stamped", before=["a"], after=["b"], reconciled=["x"]).summary() == "schema stamped: a -> b, 1 reconciled"
        assert SchemaReport(state="empty").summary() == "schema empty: none -> none, 0 reconciled"


class TestDeferredIndexes:
    @staticmethod
    def _engine(execute_side_effect=None):
        engine = MagicMock()
        connection = engine.connect.return_value.execution_options.return_value.__enter__.return_value
        connection.dialect.identifier_preparer.quote = lambda name: f'"{name}"'
        if execute_side_effect is not None:
            connection.execute.side_effect = execute_side_effect
        return engine, connection

    def test_nothing_to_build(self):
        engine = MagicMock()
        assert migrations.build_indexes_concurrently(engine, []) == []
        engine.connect.assert_not_called()

    def test_builds_concurrently_outside_a_transaction(self):
        engine, connection = self._engine()
        built = migrations.build_indexes_concurrently(engine, [("ix_a", "trail", ["user_id", "org_id"])])
        assert built == ["ix_a"]
        engine.connect.return_value.execution_options.assert_called_once_with(isolation_level="AUTOCOMMIT")
        assert str(connection.execute.call_args.args[0]) == (
            'CREATE INDEX CONCURRENTLY IF NOT EXISTS "ix_a" ON "trail" ("user_id", "org_id")'
        )

    def test_failed_build_is_dropped_and_the_rest_still_built(self, caplog):
        engine, connection = self._engine([RuntimeError("canceled"), None, None])
        with caplog.at_level(logging.WARNING):
            built = migrations.build_indexes_concurrently(engine, [("ix_a", "t", ["c"]), ("ix_b", "t", ["d"])])
        assert built == ["ix_b"]
        statements = [str(call.args[0]) for call in connection.execute.call_args_list]
        assert 'DROP INDEX CONCURRENTLY IF EXISTS "ix_a"' in statements
        assert "Could not build index ix_a" in caplog.text

    def test_failed_drop_only_logs(self, caplog):
        engine, _ = self._engine([RuntimeError("canceled"), RuntimeError("gone")])
        with caplog.at_level(logging.WARNING):
            assert migrations.build_indexes_concurrently(engine, [("ix_a", "t", ["c"])]) == []
        assert "Could not drop the invalid index ix_a" in caplog.text

    def test_runner_builds_what_migrations_deferred(self, db_url, caplog):
        ensure_schema(db_url, metadata=_models())

        def upgrade(config, target):
            config.attributes[migrations.DEFERRED_INDEXES].append(("ix_x", "widget", ["name"]))

        with patch.object(migrations.command, "upgrade", side_effect=upgrade), \
             patch.object(migrations, "build_indexes_concurrently", return_value=["ix_x"]) as build, \
             caplog.at_level(logging.INFO, logger="src.core.migrations"):
            ensure_schema(db_url, metadata=_models())
        assert build.call_args.args[1] == [("ix_x", "widget", ["name"])]
        assert "Built indexes concurrently: ix_x" in caplog.text

    def test_hand_run_alembic_has_no_deferral_list(self):
        assert migrations.DEFERRED_INDEXES not in migrations.alembic_config().attributes
        assert migrations.alembic_config(deferred_indexes=[]).attributes[migrations.DEFERRED_INDEXES] == []


class _FakeConnection:
    def __init__(self, got_lock: bool):
        self.dialect = MagicMock()
        self.dialect.name = "postgresql"
        self.got_lock = got_lock
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append(str(statement))
        result = MagicMock()
        result.scalar.return_value = self.got_lock
        return result


class TestPostgresOnlyParts:
    def test_lock_taken_at_once(self):
        conn = _FakeConnection(got_lock=True)
        with migrations._migration_lock(conn):
            pass
        assert [s.split("(")[0] for s in conn.statements] == ["SELECT pg_try_advisory_lock", "SELECT pg_advisory_unlock"]

    def test_lock_waits_for_another_instance(self, caplog):
        conn = _FakeConnection(got_lock=False)
        with caplog.at_level(logging.INFO), migrations._migration_lock(conn):
            pass
        assert "SELECT pg_advisory_lock(:k)" in conn.statements
        assert "waiting" in caplog.text

    def test_pgvector_missing_only_warns(self, caplog):
        engine = MagicMock()
        engine.dialect.name = "postgresql"
        engine.begin.side_effect = RuntimeError("no vector")
        with caplog.at_level(logging.WARNING):
            migrations._ensure_extensions(engine)
        assert "pgvector extension not available" in caplog.text

    def test_pgvector_created(self):
        engine = MagicMock()
        engine.dialect.name = "postgresql"
        migrations._ensure_extensions(engine)
        executed = engine.begin.return_value.__enter__.return_value.execute.call_args.args[0]
        assert "CREATE EXTENSION IF NOT EXISTS vector" in str(executed)


class TestStartupHook:
    async def test_startup_migrates_when_enabled(self, monkeypatch):
        from src.core.events import database

        monkeypatch.setattr(database, "is_testing", False)
        monkeypatch.delenv("LEARNHOUSE_AUTO_MIGRATE", raising=False)
        with patch("src.core.migrations.ensure_schema") as ensure:
            await database._bootstrap_schema()
        ensure.assert_called_once_with()

    async def test_failed_migration_is_not_retried(self, monkeypatch):
        from src.core.events import database

        calls = []

        async def failing():
            calls.append(1)
            raise SchemaMigrationError("bad")

        monkeypatch.setattr(database, "_bootstrap_schema", failing)
        with pytest.raises(SchemaMigrationError):
            await database.connect_to_db(MagicMock())
        assert len(calls) == 1
