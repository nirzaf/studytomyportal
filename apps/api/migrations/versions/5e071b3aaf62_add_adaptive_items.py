"""Add exact adaptive assessment item snapshots.

Revision ID: 5e071b3aaf62
Revises: 472f0b3ca571
"""

import sqlalchemy as sa
from alembic import op

from src.core.adaptive_item_schema import ensure_adaptive_item_guard

revision = "5e071b3aaf62"
down_revision = "472f0b3ca571"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "st_item_revision",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uuid", sa.String(length=64), nullable=False),
        sa.Column("item_family_uuid", sa.String(length=64), nullable=False),
        sa.Column("curriculum_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("concept_id", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=False),
        sa.Column("course_uuid", sa.String(length=255), nullable=False),
        sa.Column("assignment_uuid", sa.String(length=100), nullable=False),
        sa.Column("source_task_uuid", sa.String(length=100), nullable=False),
        sa.Column("source_question_uuid", sa.String(length=100), nullable=True),
        sa.Column("source_digest", sa.String(length=64), nullable=False),
        sa.Column("source_exposure", sa.String(length=32), nullable=False),
        sa.Column("evaluator_version", sa.String(length=40), nullable=False),
        sa.Column("rights_reference", sa.String(length=500), nullable=False),
        sa.Column("public_question", sa.JSON(), nullable=False),
        sa.Column("private_answer", sa.JSON(), nullable=False),
        sa.Column("hint_ladder", sa.JSON(), nullable=False),
        sa.Column("error_codes", sa.JSON(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_digest", sa.String(length=64), nullable=True),
        sa.Column("policy_version_ids", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(status = 'draft' AND reviewer_id IS NULL AND approved_at IS NULL AND content_digest IS NULL) OR (status IN ('published', 'retired') AND reviewer_id IS NOT NULL AND approved_at IS NOT NULL AND content_digest IS NOT NULL AND length(content_digest) = 64)",
            name="ck_st_item_review",
        ),
        sa.CheckConstraint(
            "kind IN ('single_choice', 'number')", name="ck_st_item_kind"
        ),
        sa.CheckConstraint(
            "source_exposure IN ('possible_native_reveal', 'unverified_native_history')",
            name="ck_st_item_native_exposure",
        ),
        sa.CheckConstraint(
            "length(source_digest) = 64", name="ck_st_item_source_digest"
        ),
        sa.CheckConstraint(
            "revision > 0 AND version > 0 AND difficulty BETWEEN 1 AND 5",
            name="ck_st_item_version",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "curriculum_revision_id", "concept_id"],
            ["st_concept.org_id", "st_concept.curriculum_revision_id", "st_concept.id"],
            name="fk_st_item_concept",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "item_family_uuid", "revision", name="uq_st_item_family_revision"
        ),
        sa.UniqueConstraint("uuid", name="uq_st_item_uuid"),
    )

    ensure_adaptive_item_guard(op.get_bind())


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("LOCK TABLE st_item_revision IN ACCESS EXCLUSIVE MODE")
        )
    if connection.execute(sa.text("SELECT 1 FROM st_item_revision LIMIT 1")).first():
        raise RuntimeError(
            "Retained item revisions exist. Roll back application code and retain the schema."
        )
    op.drop_table("st_item_revision")
    if connection.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS guard_st_item_revision()")
