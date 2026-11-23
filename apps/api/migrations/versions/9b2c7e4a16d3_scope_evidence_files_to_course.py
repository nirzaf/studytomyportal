"""Allow private evidence for an exact course revision.

Revision ID: 9b2c7e4a16d3
Revises: 61b26e413fa2
"""

import sqlalchemy as sa
from alembic import op

revision = "9b2c7e4a16d3"
down_revision = "61b26e413fa2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("st_evidence_file") as batch:
        batch.add_column(sa.Column("course_uuid", sa.String(100)))
        batch.add_column(sa.Column("revision_sha256", sa.String(64)))
        batch.alter_column("learner_profile_id", nullable=True)
        batch.drop_constraint("ck_st_evidence_file_purpose", type_="check")
        batch.create_check_constraint(
            "ck_st_evidence_file_purpose",
            "purpose IN ('eligibility_evidence', 'content_rights', 'content_accessibility')",
        )
        batch.create_check_constraint(
            "ck_st_evidence_file_scope",
            "(purpose = 'eligibility_evidence' AND learner_profile_id IS NOT NULL "
            "AND course_uuid IS NULL AND revision_sha256 IS NULL) OR "
            "(purpose IN ('content_rights', 'content_accessibility') AND learner_profile_id IS NULL "
            "AND course_uuid IS NOT NULL AND revision_sha256 IS NOT NULL AND length(revision_sha256) = 64)",
        )
        batch.create_index("ix_st_evidence_file_course", ["org_id", "course_uuid", "revision_sha256"])


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE st_evidence_file IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT 1 FROM st_evidence_file WHERE course_uuid IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Retained course evidence exists. Roll back application code without dropping evidence.")
    with op.batch_alter_table("st_evidence_file") as batch:
        batch.drop_index("ix_st_evidence_file_course")
        batch.drop_constraint("ck_st_evidence_file_scope", type_="check")
        batch.drop_constraint("ck_st_evidence_file_purpose", type_="check")
        batch.create_check_constraint("ck_st_evidence_file_purpose", "purpose = 'eligibility_evidence'")
        batch.alter_column("learner_profile_id", nullable=False)
        batch.drop_column("revision_sha256")
        batch.drop_column("course_uuid")
