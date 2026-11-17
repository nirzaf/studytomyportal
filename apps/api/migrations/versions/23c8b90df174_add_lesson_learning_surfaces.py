"""Add durable ungraded lesson learning surfaces.

Revision ID: 23c8b90df174
Revises: 8b53c0d1e6a4
"""

import sqlalchemy as sa
from alembic import op

revision = "23c8b90df174"
down_revision = "8b53c0d1e6a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "st_learning_surface",
        sa.Column("surface_uuid", sa.String(36), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_uuid", sa.String(36), nullable=False),
        sa.Column("course_uuid", sa.String(255), nullable=False),
        sa.Column("activity_uuid", sa.String(89), nullable=False),
        sa.Column("source_identity", sa.String(64), nullable=False),
        sa.Column("presentation", sa.JSON(), nullable=False),
        sa.Column("evaluation_basis", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("org_id", "user_id", "request_uuid", name="uq_st_learning_surface_request"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM st_learning_surface LIMIT 1")).first() is not None:
        raise RuntimeError("Retained lesson practice exists. Roll back application code without dropping evidence.")
    op.drop_table("st_learning_surface")
