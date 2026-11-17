"""Bind private evidence metadata to a record-request intake event."""

import sqlalchemy as sa
from alembic import op

from src.core.studytomy_schema import RECORD_REQUEST_EVIDENCE_GUARD

revision = "4a9f72c6e801"
down_revision = "b2c71f4a6089"
branch_labels = None
depends_on = None

_PURPOSE = "purpose IN ('eligibility_evidence', 'content_rights', 'content_accessibility')"
_LEARNER = "purpose = 'eligibility_evidence' AND learner_profile_id IS NOT NULL AND course_uuid IS NULL AND revision_sha256 IS NULL"
_COURSE = (
    "purpose IN ('content_rights', 'content_accessibility') AND learner_profile_id IS NULL "
    "AND course_uuid IS NOT NULL AND revision_sha256 IS NOT NULL AND length(revision_sha256) = 64"
)

def upgrade() -> None:
    with op.batch_alter_table("st_business_event") as batch:
        batch.create_unique_constraint("uq_st_business_event_org_event_uuid", ["org_id", "event_uuid"])
    with op.batch_alter_table("st_evidence_file") as batch:
        batch.add_column(sa.Column("record_request_event_uuid", sa.String(100)))
        batch.drop_constraint("ck_st_evidence_file_purpose", type_="check")
        batch.drop_constraint("ck_st_evidence_file_scope", type_="check")
        batch.create_check_constraint(
            "ck_st_evidence_file_purpose",
            "purpose IN ('eligibility_evidence', 'content_rights', 'content_accessibility', 'record_request_evidence')",
        )
        batch.create_check_constraint(
            "ck_st_evidence_file_scope",
            f"({_LEARNER} AND record_request_event_uuid IS NULL) OR "
            f"({_COURSE} AND record_request_event_uuid IS NULL) OR "
            "(purpose = 'record_request_evidence' AND learner_profile_id IS NOT NULL "
            "AND course_uuid IS NULL AND revision_sha256 IS NULL AND record_request_event_uuid IS NOT NULL)",
        )
        batch.create_foreign_key(
            "fk_st_evidence_file_record_request", "st_business_event",
            ["org_id", "record_request_event_uuid"], ["org_id", "event_uuid"], ondelete="RESTRICT",
        )
        batch.create_index("ix_st_evidence_file_record_request", ["org_id", "record_request_event_uuid"])
    if op.get_bind().dialect.name == "postgresql":
        _, _, function_sql, trigger_sql = RECORD_REQUEST_EVIDENCE_GUARD
        op.execute(function_sql)
        op.execute(trigger_sql)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE st_evidence_file IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text(
        "SELECT 1 FROM st_evidence_file WHERE record_request_event_uuid IS NOT NULL LIMIT 1"
    )).first():
        raise RuntimeError("Retained record request evidence exists. Roll back application code without dropping evidence.")
    if connection.dialect.name == "postgresql":
        op.execute("DROP TRIGGER trg_st_record_request_evidence ON st_evidence_file")
        op.execute("DROP FUNCTION check_st_record_request_evidence()")
    with op.batch_alter_table("st_evidence_file") as batch:
        batch.drop_index("ix_st_evidence_file_record_request")
        batch.drop_constraint("fk_st_evidence_file_record_request", type_="foreignkey")
        batch.drop_constraint("ck_st_evidence_file_scope", type_="check")
        batch.drop_constraint("ck_st_evidence_file_purpose", type_="check")
        batch.create_check_constraint("ck_st_evidence_file_purpose", _PURPOSE)
        batch.create_check_constraint("ck_st_evidence_file_scope", f"({_LEARNER}) OR ({_COURSE})")
        batch.drop_column("record_request_event_uuid")
    with op.batch_alter_table("st_business_event") as batch:
        batch.drop_constraint("uq_st_business_event_org_event_uuid", type_="unique")
