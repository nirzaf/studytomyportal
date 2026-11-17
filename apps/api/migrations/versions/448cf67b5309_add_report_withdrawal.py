"""Add report withdrawal and correction lineage.

Revision ID: 448cf67b5309
Revises: b2460ac8149e
"""

import sqlalchemy as sa
from alembic import op

revision = "448cf67b5309"
down_revision = "b2460ac8149e"
branch_labels = None
depends_on = None


def _review_constraint(batch, statuses: str) -> None:
    batch.create_check_constraint(
        "ck_st_progress_report_review",
        "(status = 'draft' AND reviewer_user_id IS NULL AND approved_at IS NULL) OR "
        f"(status IN ({statuses}) AND reviewer_user_id IS NOT NULL AND approved_at IS NOT NULL)",
    )


def upgrade() -> None:
    with op.batch_alter_table("st_progress_report") as batch:
        batch.drop_constraint("ck_st_progress_report_review", type_="check")
        batch.add_column(sa.Column("change_reason", sa.String(32)))
        batch.add_column(sa.Column("changed_by_user_id", sa.Integer()))
        batch.create_foreign_key(
            "fk_st_progress_report_changed_by",
            "user",
            ["changed_by_user_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            "ck_st_progress_report_change",
            "(change_reason IS NULL AND changed_by_user_id IS NULL AND status <> 'withdrawn') OR "
            "(change_reason IS NOT NULL AND changed_by_user_id IS NOT NULL "
            "AND change_reason IN ('evidence_corrected','disclosure_authority','report_error','other'))",
        )
        _review_constraint(batch, "'approved', 'published', 'withdrawn'")


def downgrade() -> None:
    report = sa.table("st_progress_report", sa.column("change_reason", sa.String(32)))
    changed = sa.select(sa.exists().where(report.c.change_reason.is_not(None)))
    if op.get_bind().scalar(changed):
        raise RuntimeError("Downgrade would discard protected report correction history.")
    with op.batch_alter_table("st_progress_report") as batch:
        batch.drop_constraint("ck_st_progress_report_review", type_="check")
        batch.drop_constraint("ck_st_progress_report_change", type_="check")
        batch.drop_constraint("fk_st_progress_report_changed_by", type_="foreignkey")
        batch.drop_column("changed_by_user_id")
        batch.drop_column("change_reason")
        _review_constraint(batch, "'approved', 'published'")
