"""Add immutable diagnostic definition revisions.

Revision ID: 7bfc5ca4ad19
Revises: 19c0a82d4fe6
"""

import sqlalchemy as sa
from alembic import op

from src.core.adaptive_diagnostic_schema import ensure_adaptive_diagnostic_guard

revision = "7bfc5ca4ad19"
down_revision = "19c0a82d4fe6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "st_diagnostic_revision",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uuid", sa.String(length=64), nullable=False),
        sa.Column("blueprint_uuid", sa.String(length=64), nullable=False),
        sa.Column("curriculum_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("selector_version", sa.String(length=40), nullable=False),
        sa.Column("mastery_policy_uuid", sa.String(length=100), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_digest", sa.String(length=64), nullable=True),
        sa.Column("policy_version_ids", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(status = 'draft' AND reviewer_id IS NULL AND approved_at IS NULL AND content_digest IS NULL) OR (status IN ('published', 'retired') AND reviewer_id IS NOT NULL AND approved_at IS NOT NULL AND content_digest IS NOT NULL AND length(content_digest) = 64)",
            name="ck_st_diagnostic_review",
        ),
        sa.CheckConstraint(
            "revision >= 1 AND version >= 1", name="ck_st_diagnostic_version"
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "curriculum_revision_id"],
            ["st_curriculum_revision.org_id", "st_curriculum_revision.id"],
            name="fk_st_diagnostic_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "blueprint_uuid", "revision", name="uq_st_diagnostic_revision"
        ),
        sa.UniqueConstraint("uuid", name="uq_st_diagnostic_uuid"),
    )

    ensure_adaptive_diagnostic_guard(op.get_bind())


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("LOCK TABLE st_diagnostic_revision IN ACCESS EXCLUSIVE MODE")
        )
    if connection.execute(
        sa.text("SELECT 1 FROM st_diagnostic_revision LIMIT 1")
    ).first():
        raise RuntimeError(
            "Retained diagnostic revisions exist. Roll back application code and retain the schema."
        )
    op.drop_table("st_diagnostic_revision")
    if connection.dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS guard_st_diagnostic_revision()")
