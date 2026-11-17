"""Add versioned progress report drafts.

Revision ID: 8617afa045b0
Revises: ef0b241a1c57
"""

import sqlalchemy as sa
from alembic import op

revision = "8617afa045b0"
down_revision = "ef0b241a1c57"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "st_progress_report",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True
        ),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organization.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uuid", sa.String(64), nullable=False),
        sa.Column("learner_profile_id", sa.BigInteger(), nullable=False),
        sa.Column("curriculum_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("owner_course_uuid", sa.String(255), nullable=False),
        sa.Column(
            "requester_user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("operation_uuid", sa.String(36), nullable=False),
        sa.Column("period_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_watermark", sa.BigInteger(), nullable=False),
        sa.Column("policy_version_ids", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ("org_id", "learner_profile_id"),
            ("st_learner_profile.org_id", "st_learner_profile.id"),
            name="fk_st_report_learner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ("org_id", "curriculum_revision_id"),
            ("st_curriculum_revision.org_id", "st_curriculum_revision.id"),
            name="fk_st_report_curriculum",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("uuid", "version", name="uq_st_progress_report_version"),
        sa.UniqueConstraint(
            "org_id",
            "requester_user_id",
            "operation_uuid",
            name="uq_st_progress_report_operation",
        ),
        sa.CheckConstraint(
            "period_from < period_to AND version >= 1 AND evidence_watermark >= 0 AND length(digest) = 64",
            name="ck_st_progress_report_snapshot",
        ),
        sa.CheckConstraint("status = 'draft'", name="ck_st_progress_report_draft"),
    )


def downgrade() -> None:
    op.drop_table("st_progress_report")
