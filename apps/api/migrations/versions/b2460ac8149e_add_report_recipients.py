"""Store explicitly authorized report recipient projections.

Revision ID: b2460ac8149e
Revises: 91948e1f3a2d
"""

import sqlalchemy as sa
from alembic import op

revision = "b2460ac8149e"
down_revision = "91948e1f3a2d"
branch_labels = None
depends_on = None


def _status(earlier: bool) -> None:
    approved = (
        "status = 'approved'" if earlier else "status IN ('approved', 'published')"
    )
    with op.batch_alter_table("st_progress_report") as batch:
        batch.drop_constraint("ck_st_progress_report_review", type_="check")
        batch.create_check_constraint(
            "ck_st_progress_report_review",
            "(status = 'draft' AND reviewer_user_id IS NULL AND approved_at IS NULL) OR "
            f"({approved} AND reviewer_user_id IS NOT NULL AND approved_at IS NOT NULL)",
        )


def upgrade() -> None:
    _status(False)
    op.create_table(
        "st_report_recipient",
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
        sa.Column(
            "report_id",
            sa.BigInteger(),
            sa.ForeignKey("st_progress_report.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "recipient_user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "sharing_grant_id",
            sa.BigInteger(),
            sa.ForeignKey("st_sharing_grant.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("grant_version", sa.Integer(), nullable=False),
        sa.Column("projection", sa.String(64), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "report_id",
            "recipient_user_id",
            "projection",
            name="uq_st_report_recipient_projection",
        ),
        sa.CheckConstraint(
            "projection = 'adaptive_report_summary' AND grant_version >= 1",
            name="ck_st_report_recipient_scope",
        ),
    )


def downgrade() -> None:
    op.drop_table("st_report_recipient")
    _status(True)
