"""Version outbox rows for guarded reconciliation decisions."""

import sqlalchemy as sa
from alembic import op


revision = "9b7d4c2a1e6f"
down_revision = "f3c9a6e1b204"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "st_outbox_record",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_check_constraint(
        "ck_st_outbox_record_version",
        "st_outbox_record",
        "version >= 1",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_st_outbox_record_version", "st_outbox_record", type_="check"
    )
    op.drop_column("st_outbox_record", "version")
