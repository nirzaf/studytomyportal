"""Retain refund reservation approvals against confirmed collections."""

import sqlalchemy as sa
from alembic import op

from src.core.studytomy_schema import REFUND_RESERVATION_GUARD

revision = "c0f63d7b29a4"
down_revision = "425be90c73a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("st_money_movement") as batch:
        batch.create_unique_constraint(
            "uq_st_money_movement_refund_scope", ["org_id", "order_id", "id", "currency"],
        )
    op.create_table(
        "st_refund_reservation",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("movement_id", sa.BigInteger(), nullable=False),
        sa.Column("refund_reference", sa.String(36), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("approved_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("remedy_policy_id", sa.Integer(), sa.ForeignKey("st_policy_version.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("finance_policy_id", sa.Integer(), sa.ForeignKey("st_policy_version.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "order_id", "movement_id", "currency"],
            ["st_money_movement.org_id", "st_money_movement.order_id",
             "st_money_movement.id", "st_money_movement.currency"],
            name="fk_st_refund_reservation_collection", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("org_id", "refund_reference", name="uq_st_refund_reservation_reference"),
        sa.CheckConstraint("amount_minor > 0", name="ck_st_refund_reservation_amount"),
        sa.CheckConstraint("length(reason_code) > 0", name="ck_st_refund_reservation_reason"),
        sa.CheckConstraint("remedy_policy_id <> finance_policy_id", name="ck_st_refund_reservation_policies"),
    )
    op.create_index("ix_st_refund_reservation_movement_id", "st_refund_reservation", ["movement_id"])
    if op.get_bind().dialect.name == "postgresql":
        _, _, function_sql, trigger_sql = REFUND_RESERVATION_GUARD
        op.execute(function_sql)
        op.execute(trigger_sql)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT 1 FROM st_refund_reservation LIMIT 1")) is not None:
        raise RuntimeError("Retain refund reservation history during rollback")
    op.drop_table("st_refund_reservation")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION protect_st_refund_reservation()")
    with op.batch_alter_table("st_money_movement") as batch:
        batch.drop_constraint("uq_st_money_movement_refund_scope", type_="unique")
