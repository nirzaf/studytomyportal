"""Bind native media digests to new course approvals.

Revision ID: 08fd9332b6ac
Revises: 627b1fd4a981
"""

import sqlalchemy as sa
from alembic import op

revision = "08fd9332b6ac"
down_revision = "627b1fd4a981"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "st_course_review", sa.Column("media_manifest", sa.JSON(none_as_null=True))
    )


def downgrade() -> None:
    connection = op.get_bind()
    if (
        connection.scalar(
            sa.text(
                "SELECT 1 FROM st_course_review WHERE media_manifest IS NOT NULL LIMIT 1"
            )
        )
        is not None
    ):
        raise RuntimeError("Retain reviewed course media manifests during rollback")
    op.drop_column("st_course_review", "media_manifest")
