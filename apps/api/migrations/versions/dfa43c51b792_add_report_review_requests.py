"""Record bounded, version-specific report review requests.

Revision ID: dfa43c51b792
Revises: 448cf67b5309
"""

import sqlalchemy as sa
from alembic import op

revision = "dfa43c51b792"
down_revision = "448cf67b5309"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "st_report_review_request",
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
        sa.Column(
            "report_id",
            sa.BigInteger(),
            sa.ForeignKey("st_progress_report.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "requester_user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.UniqueConstraint("uuid", name="uq_st_report_review_request_uuid"),
        sa.UniqueConstraint(
            "report_id",
            "requester_user_id",
            "reason",
            name="uq_st_report_review_request_reason",
        ),
        sa.CheckConstraint(
            "reason IN ('evidence_error','report_inaccurate','privacy_concern','other')",
            name="ck_st_report_review_request_reason",
        ),
    )


def downgrade() -> None:
    request = sa.table("st_report_review_request")
    if op.get_bind().scalar(sa.select(sa.exists().select_from(request))):
        raise RuntimeError("Downgrade would discard protected report review requests.")
    op.drop_table("st_report_review_request")
