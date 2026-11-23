"""Bind Playground review to stored thumbnail bytes.

Revision ID: b7c2e9a4d1f0
Revises: e7f2c9a4b138
"""

import sqlalchemy as sa
from alembic import op

revision = "b7c2e9a4d1f0"
down_revision = "e7f2c9a4b138"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "playground",
        sa.Column("thumbnail_content_sha256", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("playground", "thumbnail_content_sha256")
