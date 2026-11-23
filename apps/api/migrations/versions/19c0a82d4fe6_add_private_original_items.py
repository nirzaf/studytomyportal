"""Allow private original adaptive items with exact provenance.

Revision ID: 19c0a82d4fe6
Revises: 5e071b3aaf62
"""

import sqlalchemy as sa
from alembic import op

revision = "19c0a82d4fe6"
down_revision = "5e071b3aaf62"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("st_item_revision") as batch:
        batch.drop_constraint("ck_st_item_native_exposure", type_="check")
        batch.alter_column(
            "assignment_uuid", existing_type=sa.String(100), nullable=True
        )
        batch.alter_column(
            "source_task_uuid", existing_type=sa.String(100), nullable=True
        )
        batch.create_check_constraint(
            "ck_st_item_source_provenance",
            "(source_exposure IN ('possible_native_reveal', 'unverified_native_history')"
            " AND assignment_uuid IS NOT NULL AND source_task_uuid IS NOT NULL)"
            " OR (source_exposure = 'private_original' AND assignment_uuid IS NULL"
            " AND source_task_uuid IS NULL AND source_question_uuid IS NULL)",
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("LOCK TABLE st_item_revision IN ACCESS EXCLUSIVE MODE")
        )
    if connection.execute(
        sa.text(
            "SELECT 1 FROM st_item_revision WHERE source_exposure = 'private_original' LIMIT 1"
        )
    ).first():
        raise RuntimeError(
            "Retained original items exist. Roll back application code and retain the schema."
        )
    with op.batch_alter_table("st_item_revision") as batch:
        batch.drop_constraint("ck_st_item_source_provenance", type_="check")
        batch.alter_column(
            "assignment_uuid", existing_type=sa.String(100), nullable=False
        )
        batch.alter_column(
            "source_task_uuid", existing_type=sa.String(100), nullable=False
        )
        batch.create_check_constraint(
            "ck_st_item_native_exposure",
            "source_exposure IN ('possible_native_reveal', 'unverified_native_history')",
        )
