"""Add durable per-user session revocation generations."""

import sqlalchemy as sa
from alembic import op


revision = "c2d8e91f4a70"
down_revision = "b91d2f6a08c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column(
            "session_generation",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.execute('UPDATE "user" SET session_generation = 1 WHERE session_generation IS NULL')
    op.alter_column("user", "session_generation", server_default=None)


def downgrade() -> None:
    raise RuntimeError(
        "Durable session revocation is security-sensitive and cannot be downgraded "
        "to Redis-only revocation."
    )
