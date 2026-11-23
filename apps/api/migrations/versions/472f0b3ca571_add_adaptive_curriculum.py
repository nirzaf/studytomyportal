"""Add adaptive curriculum definitions.

Revision ID: 472f0b3ca571
Revises: 9b2c7e4a16d3
"""

import sqlalchemy as sa
from alembic import op

from src.core.adaptive_curriculum_schema import ensure_adaptive_curriculum_guards

revision = "472f0b3ca571"
down_revision = "9b2c7e4a16d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "st_curriculum_revision",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uuid", sa.String(length=64), nullable=False),
        sa.Column("curriculum_key", sa.String(length=100), nullable=False),
        sa.Column("owner_course_uuid", sa.String(length=255), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("subject", sa.String(length=120), nullable=False),
        sa.Column("level", sa.String(length=80), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("jurisdiction", sa.String(length=120), nullable=True),
        sa.Column("source_reference", sa.String(length=500), nullable=False),
        sa.Column("rights_reference", sa.String(length=500), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_digest", sa.String(length=64), nullable=True),
        sa.Column("policy_version_ids", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(status = 'draft' AND reviewer_id IS NULL AND approved_at IS NULL AND content_digest IS NULL) OR (status IN ('published', 'retired') AND reviewer_id IS NOT NULL AND approved_at IS NOT NULL AND content_digest IS NOT NULL AND length(content_digest) = 64 AND length(rights_reference) > 0)",
            name="ck_st_curriculum_review",
        ),
        sa.CheckConstraint(
            "revision > 0 AND version > 0", name="ck_st_curriculum_version"
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "curriculum_key", "revision", name="uq_st_curriculum_revision"
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_st_curriculum_revision_parent"),
        sa.UniqueConstraint("uuid", name="uq_st_curriculum_revision_uuid"),
    )
    op.create_table(
        "st_concept",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uuid", sa.String(length=64), nullable=False),
        sa.Column("curriculum_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("objective", sa.String(length=2000), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.CheckConstraint("sequence >= 0", name="ck_st_concept_sequence"),
        sa.ForeignKeyConstraint(
            ["org_id", "curriculum_revision_id"],
            ["st_curriculum_revision.org_id", "st_curriculum_revision.id"],
            name="fk_st_concept_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "curriculum_revision_id", "code", name="uq_st_concept_code"
        ),
        sa.UniqueConstraint(
            "org_id", "curriculum_revision_id", "id", name="uq_st_concept_parent"
        ),
        sa.UniqueConstraint("uuid", name="uq_st_concept_uuid"),
    )
    op.create_table(
        "st_concept_prerequisite",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uuid", sa.String(length=64), nullable=False),
        sa.Column("curriculum_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("concept_id", sa.BigInteger(), nullable=False),
        sa.Column("prerequisite_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "concept_id <> prerequisite_id", name="ck_st_concept_edge_no_self"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "curriculum_revision_id", "concept_id"],
            ["st_concept.org_id", "st_concept.curriculum_revision_id", "st_concept.id"],
            name="fk_st_prerequisite_concept",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "curriculum_revision_id", "prerequisite_id"],
            ["st_concept.org_id", "st_concept.curriculum_revision_id", "st_concept.id"],
            name="fk_st_prerequisite_target",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("concept_id", "prerequisite_id", name="uq_st_concept_edge"),
        sa.UniqueConstraint("uuid", name="uq_st_concept_prerequisite_uuid"),
    )
    op.create_index(
        "ix_st_prerequisite_target",
        "st_concept_prerequisite",
        ["prerequisite_id"],
        unique=False,
    )
    op.create_table(
        "st_concept_activity",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uuid", sa.String(length=64), nullable=False),
        sa.Column("curriculum_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("concept_id", sa.BigInteger(), nullable=False),
        sa.Column("course_id", sa.Integer(), nullable=False),
        sa.Column("course_uuid", sa.String(length=255), nullable=False),
        sa.Column("activity_id", sa.Integer(), nullable=False),
        sa.Column("activity_uuid", sa.String(length=89), nullable=False),
        sa.Column("activity_version", sa.Integer(), nullable=False),
        sa.Column("activity_digest", sa.String(length=64), nullable=False),
        sa.Column("usage", sa.String(length=12), nullable=False),
        sa.Column("review_reference", sa.String(length=500), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "usage IN ('teach', 'practice', 'review')",
            name="ck_st_concept_activity_usage",
        ),
        sa.CheckConstraint(
            "activity_version >= 1 AND length(activity_digest) = 64",
            name="ck_st_concept_activity_revision",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "curriculum_revision_id", "concept_id"],
            ["st_concept.org_id", "st_concept.curriculum_revision_id", "st_concept.id"],
            name="fk_st_concept_activity_concept",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "concept_id", "activity_uuid", "usage", name="uq_st_concept_activity"
        ),
        sa.UniqueConstraint("uuid", name="uq_st_concept_activity_uuid"),
    )
    op.create_index(
        "ix_st_concept_activity_lesson",
        "st_concept_activity",
        ["org_id", "activity_uuid"],
        unique=False,
    )

    ensure_adaptive_curriculum_guards(op.get_bind())


def downgrade() -> None:
    connection = op.get_bind()
    tables = (
        "st_concept_activity",
        "st_concept_prerequisite",
        "st_concept",
        "st_curriculum_revision",
    )
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("LOCK TABLE st_curriculum_revision IN ACCESS EXCLUSIVE MODE")
        )
    if connection.execute(
        sa.text("SELECT 1 FROM st_curriculum_revision LIMIT 1")
    ).first():
        raise RuntimeError(
            "Curriculum definitions exist. Roll back application code and retain the schema."
        )
    for table in tables:
        op.drop_table(table)
    if connection.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS guard_st_curriculum_child()")
        op.execute("DROP FUNCTION IF EXISTS guard_st_curriculum_revision()")
