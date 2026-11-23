"""Add nullable learner_adaptation JSON column to playground."""

import sqlalchemy as sa
from alembic import op


revision = "c4e8a1b7d209"
down_revision = "9b7d4c2a1e6f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "playground",
        sa.Column("learner_adaptation", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("playground", "learner_adaptation")
