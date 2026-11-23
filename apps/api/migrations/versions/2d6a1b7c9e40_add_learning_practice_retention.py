"""Bind ungraded lesson practice to its approved retention horizon.

Revision ID: 2d6a1b7c9e40
Revises: f04d9c6a31b8
"""

import sqlalchemy as sa
from alembic import op

revision = "2d6a1b7c9e40"
down_revision = "f04d9c6a31b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("st_learning_surface", "st_learning_action"):
        op.add_column(
            table,
            sa.Column(
                "retention_policy_id",
                sa.Integer(),
                sa.ForeignKey("st_policy_version.id", ondelete="RESTRICT"),
                nullable=True,
            ),
        )
        op.add_column(table, sa.Column("retention_category", sa.String(length=64), nullable=True))
        op.add_column(table, sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True))
        expiry_check = (
            "retention_expires_at > created_at"
            if table == "st_learning_surface"
            else "retention_expires_at IS NOT NULL"
        )
        op.create_check_constraint(
            f"ck_{table}_retention_binding",
            table,
            "(retention_policy_id IS NULL AND retention_category IS NULL AND retention_expires_at IS NULL) OR "
            "(retention_policy_id IS NOT NULL AND retention_category = 'lesson_visual_practice' "
            f"AND {expiry_check})",
        )
    op.create_index(
        "ix_st_learning_surface_retention_expiry",
        "st_learning_surface",
        ["org_id", "retention_policy_id", "retention_expires_at"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    for table in ("st_learning_action", "st_learning_surface"):
        if connection.execute(sa.text(f"SELECT 1 FROM {table} WHERE retention_policy_id IS NOT NULL LIMIT 1")).first():
            raise RuntimeError(
                "Lesson practice retention decisions exist. Roll back application code without dropping retention evidence."
            )
    op.drop_index("ix_st_learning_surface_retention_expiry", table_name="st_learning_surface")
    for table in ("st_learning_action", "st_learning_surface"):
        op.drop_constraint(f"ck_{table}_retention_binding", table, type_="check")
        op.drop_column(table, "retention_expires_at")
        op.drop_column(table, "retention_category")
        op.drop_column(table, "retention_policy_id")
