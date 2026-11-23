"""Add durable tutor turn reservations and recovery.

Revision ID: 950ec32a74bf
Revises: 37e0adb59ca1
"""

import sqlalchemy as sa
from alembic import op

revision = "950ec32a74bf"
down_revision = "37e0adb59ca1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("st_tutor_session") as batch:
        batch.create_unique_constraint("uq_st_tutor_session_org_id", ["org_id", "id"])
    op.create_table(
        "st_tutor_turn",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True
        ),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organization.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ("org_id", "session_id"),
            ("st_tutor_session.org_id", "st_tutor_session.id"),
            name="fk_st_tutor_turn_session",
            ondelete="CASCADE",
        ),
        sa.Column("operation_uuid", sa.String(36), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("requested_version", sa.Integer(), nullable=False),
        sa.Column("credit_used", sa.Integer()),
        sa.Column("result", sa.JSON(none_as_null=True)),
        sa.Column("result_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "session_id", "operation_uuid", name="uq_st_tutor_turn_operation"
        ),
        sa.CheckConstraint(
            "state IN ('running', 'completed', 'failed', 'reconciliation_required', 'cancelled') "
            "AND action IN ('explain', 'hint', 'check_understanding') "
            "AND requested_version >= 1 AND length(request_digest) = 64 "
            "AND (credit_used IS NULL OR credit_used >= 0) "
            "AND (state = 'completed' OR result IS NULL)",
            name="ck_st_tutor_turn_state",
        ),
    )
    op.create_index(
        "ix_st_tutor_turn_session_created",
        "st_tutor_turn",
        ["session_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("st_tutor_turn")
    with op.batch_alter_table("st_tutor_session") as batch:
        batch.drop_constraint("uq_st_tutor_session_org_id", type_="unique")
