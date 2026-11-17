"""Add exact-version staff review to learner report drafts.

Revision ID: 91948e1f3a2d
Revises: 8617afa045b0
"""

import sqlalchemy as sa
from alembic import op

revision = "91948e1f3a2d"
down_revision = "8617afa045b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("st_progress_report") as batch:
        batch.drop_constraint("ck_st_progress_report_draft", type_="check")
        batch.alter_column("operation_uuid", existing_type=sa.String(36), nullable=True)
        batch.add_column(sa.Column("commentary", sa.String(600)))
        batch.add_column(sa.Column("reviewer_user_id", sa.Integer()))
        batch.add_column(sa.Column("approved_at", sa.DateTime(timezone=True)))
        batch.create_foreign_key(
            "fk_st_progress_report_reviewer",
            "user",
            ["reviewer_user_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_st_progress_report_review",
            "(status = 'draft' AND reviewer_user_id IS NULL AND approved_at IS NULL) OR "
            "(status = 'approved' AND reviewer_user_id IS NOT NULL AND approved_at IS NOT NULL)",
        )


def downgrade() -> None:
    with op.batch_alter_table("st_progress_report") as batch:
        batch.drop_constraint("ck_st_progress_report_review", type_="check")
        batch.drop_constraint("fk_st_progress_report_reviewer", type_="foreignkey")
        batch.drop_column("approved_at")
        batch.drop_column("reviewer_user_id")
        batch.drop_column("commentary")
        batch.alter_column(
            "operation_uuid", existing_type=sa.String(36), nullable=False
        )
        batch.create_check_constraint("ck_st_progress_report_draft", "status = 'draft'")
