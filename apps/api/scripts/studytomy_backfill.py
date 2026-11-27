"""Inventory legacy mappings and assessment evidence on disposable PostgreSQL."""

import argparse
import json
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

def managed_submission_counts(connection) -> dict[str, int]:
    from src.services.studytomy.assignment_adapter import native_round_uuid
    from src.services.studytomy.grading import RESPONSES_FROZEN_ACTION

    total = missing = 0
    with connection.execute(text("""
        SELECT a.org_id, s.assignmentusersubmission_uuid, s.attempt_number
        FROM assignmentusersubmission s
        JOIN assignment a ON a.id = s.assignment_id
        JOIN course c ON c.id = a.course_id AND c.org_id = a.org_id
        WHERE c.studytomy_managed AND s.submission_status IN ('SUBMITTED', 'GRADED', 'LATE')
    """).execution_options(yield_per=256)) as rows:
        for batch in rows.partitions(256):
            expected = [
                (org_id, f"assessment_responses:{native_round_uuid(org_id, uuid, attempt)}")
                for org_id, uuid, attempt in batch
            ]
            stored = set(connection.execute(text("""
                SELECT org_id, dedupe_key FROM st_business_event
                WHERE action = :action AND dedupe_key = ANY(:keys)
                  AND nullif(after_state ->> 'submission_content_digest', '') IS NOT NULL
            """), {"action": RESPONSES_FROZEN_ACTION, "keys": [key for _, key in expected]}))
            total += len(batch)
            missing += sum(identity not in stored for identity in expected)
    return {"managed_finalized_submissions": total, "managed_submissions_without_frozen_response_evidence": missing}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report inventory without writing (the default).")
    parser.parse_args()

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
            counts = dict(connection.execute(text("""
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
            """)).mappings().one())
            counts.update(managed_submission_counts(connection))
    finally:
        engine.dispose()
    print(json.dumps({"mode": "inventory_only", "alembic_heads": revision, **counts}, sort_keys=True))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
