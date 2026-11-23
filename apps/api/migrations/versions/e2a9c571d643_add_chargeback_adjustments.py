"""Retain confirmed external chargebacks against confirmed collections."""

import sqlalchemy as sa
from alembic import op

from src.core.studytomy_schema import CHARGEBACK_ADJUSTMENT_GUARD

revision = "e2a9c571d643"
down_revision = "3e8a91f7c062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "st_chargeback_adjustment",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("movement_id", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("external_reference", sa.String(150), nullable=False),
        sa.Column("case_reference", sa.String(150), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("remedy_policy_id", sa.Integer(), sa.ForeignKey("st_policy_version.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("finance_policy_id", sa.Integer(), sa.ForeignKey("st_policy_version.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "order_id", "movement_id", "currency"],
            ["st_money_movement.org_id", "st_money_movement.order_id",
             "st_money_movement.id", "st_money_movement.currency"],
            name="fk_st_chargeback_collection", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("org_id", "external_reference", name="uq_st_chargeback_external_reference"),
        sa.CheckConstraint("amount_minor > 0", name="ck_st_chargeback_amount"),
        sa.CheckConstraint("length(record_sha256) = 64", name="ck_st_chargeback_digest"),
        sa.CheckConstraint("effective_at <= verified_at", name="ck_st_chargeback_time"),
    )
    op.create_index("ix_st_chargeback_adjustment_movement_id", "st_chargeback_adjustment", ["movement_id"])
    if op.get_bind().dialect.name == "postgresql":
        _, _, function_sql, trigger_sql = CHARGEBACK_ADJUSTMENT_GUARD
        op.execute(function_sql)
        op.execute(trigger_sql)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT 1 FROM st_chargeback_adjustment LIMIT 1")) is not None:
        raise RuntimeError("Retain chargeback adjustment history during rollback")
    op.drop_table("st_chargeback_adjustment")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION protect_st_chargeback_adjustment()")
