"""Retain exact policy body identities with course releases.

Revision ID: 7be224c591a6
Revises: 08fd9332b6ac
"""

import sqlalchemy as sa
from alembic import op

revision = "7be224c591a6"
down_revision = "08fd9332b6ac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "st_course_release", sa.Column("policy_binding", sa.JSON(none_as_null=True))
    )


def downgrade() -> None:
    connection = op.get_bind()
    retained = sa.text(
        "SELECT 1 FROM st_course_release WHERE policy_binding IS NOT NULL LIMIT 1"
    )
    if connection.scalar(retained) is not None:
        raise RuntimeError(
            "Retain recorded course release policy bindings during rollback"
        )
    op.drop_column("st_course_release", "policy_binding")
