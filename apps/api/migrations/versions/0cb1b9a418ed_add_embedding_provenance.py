"""Identify embedding spaces on newly generated rows."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0cb1b9a418ed"
down_revision = "6ab47ca092df"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("course_embedding", sa.Column("embedding_space", sa.String(64)))
    op.add_column("course_embedding", sa.Column("provenance", sa.JSON().with_variant(JSONB(), "postgresql")))


def downgrade() -> None:
    op.drop_column("course_embedding", "provenance")
    op.drop_column("course_embedding", "embedding_space")
