"""Inventory legacy course and learner mappings on a disposable PostgreSQL copy."""

import json
import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def main() -> None:
    raw_url = os.environ.get("STUDYTOMY_TEST_DATABASE_URL")
    if not raw_url:
        raise SystemExit("Set STUDYTOMY_TEST_DATABASE_URL to a disposable PostgreSQL database.")
    try:
        url = make_url(raw_url)
    except (ArgumentError, ValueError):
        raise SystemExit("Invalid STUDYTOMY_TEST_DATABASE_URL.") from None
    if (
        url.get_backend_name() != "postgresql"
        or url.host not in {"127.0.0.1", "::1"}
        or not url.database
        or not url.database.endswith("_test")
        or url.query
    ):
        raise SystemExit("Use a loopback PostgreSQL database ending in _test, without URL query options.")

    engine = create_engine(url.set(drivername="postgresql+psycopg2"))
    try:
        with engine.begin() as connection:
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            revision = connection.execute(text("SELECT version_num FROM alembic_version ORDER BY 1")).scalars().all()
            counts = connection.execute(text("""
                SELECT
                    (SELECT count(*) FROM course WHERE studytomy_managed) AS managed_courses,
                    (SELECT count(*) FROM course WHERE NOT studytomy_managed) AS native_courses,
                    (SELECT count(*) FROM st_learner_profile) AS existing_learner_profiles,
                    (SELECT count(*) FROM (
                        SELECT DISTINCT membership.org_id, membership.user_id
                        FROM userorganization membership
                        WHERE NOT EXISTS (
                            SELECT 1 FROM st_learner_profile profile
                            WHERE profile.org_id = membership.org_id
                              AND profile.user_id = membership.user_id
                        )
                    ) AS unmapped) AS memberships_without_profile
            """)).mappings().one()
    finally:
        engine.dispose()
    print(json.dumps({"mode": "inventory_only", "alembic_heads": revision, **counts}, sort_keys=True))


if __name__ == "__main__":
    main()
