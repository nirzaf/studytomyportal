"""Opt-in, two-phase PostgreSQL 19 persistent-volume acceptance probe.

Run once with STUDYTOMY_PG19_PERSISTENCE_PHASE=seed, recreate only the
disposable database service without removing its volume, then run with phase
verify. The normal loopback and ``_test`` safeguards still apply.
"""

import os
import re

import pytest
from sqlalchemy import create_engine, text

from src.core.migrations import ensure_schema, head_revisions
from src.tests.core.test_migrations_postgres import _test_database_url


@pytest.mark.studytomy_postgres
def test_pg19_vector_probe_persists_across_service_recreation():
    phase = os.environ.get("STUDYTOMY_PG19_PERSISTENCE_PHASE")
    if phase not in {"seed", "verify"}:
        pytest.skip("Set STUDYTOMY_PG19_PERSISTENCE_PHASE to seed or verify.")

    probe_id = os.environ.get("STUDYTOMY_PG19_PERSISTENCE_ID", "")
    if not re.fullmatch(r"[0-9a-f]{32}", probe_id):
        pytest.fail("Set STUDYTOMY_PG19_PERSISTENCE_ID to a unique 32-character hex id.")

    database_url = _test_database_url().render_as_string(hide_password=False)
    if phase == "seed":
        report = ensure_schema(database_url)
        assert report.after == head_revisions()

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            version_num = connection.execute(
                text("SELECT current_setting('server_version_num')::integer")
            ).scalar_one()
            assert version_num // 10000 == 19

            extension = connection.execute(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            ).scalar_one_or_none()
            assert extension == "0.8.7"

            revisions = connection.execute(
                text("SELECT version_num FROM alembic_version ORDER BY version_num")
            ).scalars().all()
            assert revisions == head_revisions()

            table_exists = connection.execute(
                text("SELECT to_regclass('public.st_pg19_volume_persistence_probe') IS NOT NULL")
            ).scalar_one()
            if phase == "seed":
                connection.execute(text(
                    """
                    CREATE TABLE IF NOT EXISTS st_pg19_volume_persistence_probe (
                        probe_id text PRIMARY KEY,
                        revision integer NOT NULL,
                        embedding vector(3) NOT NULL
                    )
                    """
                ))
                connection.execute(text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_st_pg19_volume_persistence_probe_hnsw
                    ON st_pg19_volume_persistence_probe
                    USING hnsw (embedding vector_cosine_ops)
                    """
                ))
                connection.execute(
                    text(
                        """
                        INSERT INTO st_pg19_volume_persistence_probe (probe_id, revision, embedding)
                        VALUES (:probe_id, 1, CAST('[1,0,0]' AS vector))
                        """
                    ),
                    {"probe_id": probe_id},
                )
            else:
                assert table_exists, "The persisted probe table is missing after service recreation."

            result = connection.execute(
                text(
                    """
                    SELECT probe_id, revision, embedding::text,
                           embedding <=> CAST('[1,0,0]' AS vector) AS distance
                    FROM st_pg19_volume_persistence_probe
                    WHERE probe_id = :probe_id
                    """
                ),
                {"probe_id": probe_id},
            ).one_or_none()
            assert result is not None, "The synthetic vector row did not survive the check."
            assert result.probe_id == probe_id
            assert result.revision == 1
            assert result[2] == "[1,0,0]"
            assert float(result.distance) == 0.0
    finally:
        engine.dispose()
