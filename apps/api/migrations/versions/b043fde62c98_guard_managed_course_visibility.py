"""Add an internal managed course publication constraint.

Revision ID: b043fde62c98
Revises: 7be224c591a6
"""

import sqlalchemy as sa
from alembic import op

revision = "b043fde62c98"
down_revision = "7be224c591a6"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_course_studytomy_managed_private"


def upgrade() -> None:
    with op.batch_alter_table("course") as batch:
        batch.add_column(
            sa.Column(
                "studytomy_managed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.create_check_constraint(
            _CONSTRAINT,
            "NOT studytomy_managed OR (public IS FALSE AND published IS FALSE)",
        )


def downgrade() -> None:
    connection = op.get_bind()
    if (
        connection.scalar(
            sa.text("SELECT 1 FROM course WHERE studytomy_managed LIMIT 1")
        )
        is not None
    ):
        raise RuntimeError("Retain managed course safeguards during rollback")
    with op.batch_alter_table("course") as batch:
        batch.drop_constraint(_CONSTRAINT, type_="check")
        batch.drop_column("studytomy_managed")
