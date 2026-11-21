"""Add course_creation_draft and course.creation_draft_uuid for durable Create Course drafts.

Revision ID: c5d1e8f2a904
Revises: b7c2e9a4d1f0
"""

import sqlalchemy as sa
from alembic import op

revision = "c5d1e8f2a904"
down_revision = "b7c2e9a4d1f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "course_creation_draft",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("draft_uuid", sa.String(), nullable=False, unique=True),
        sa.Column("org_id", sa.BigInteger(), sa.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("learnings", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", sa.Text(), nullable=False, server_default=""),
        sa.Column("public", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("thumbnail_filename", sa.String(), nullable=True),
        sa.Column("thumbnail_content_type", sa.String(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("org_id", "user_id", name="uq_course_creation_draft_owner"),
    )
    op.create_index("ix_course_creation_draft_org_id", "course_creation_draft", ["org_id"])
    op.create_index("ix_course_creation_draft_user_id", "course_creation_draft", ["user_id"])

    op.add_column("course", sa.Column("creation_draft_uuid", sa.String(), nullable=True))
    op.create_unique_constraint("uq_course_creation_draft_uuid", "course", ["creation_draft_uuid"])


def downgrade() -> None:
    op.drop_constraint("uq_course_creation_draft_uuid", "course", type_="unique")
    op.drop_column("course", "creation_draft_uuid")
    op.drop_index("ix_course_creation_draft_user_id", table_name="course_creation_draft")
    op.drop_index("ix_course_creation_draft_org_id", table_name="course_creation_draft")
    op.drop_table("course_creation_draft")
