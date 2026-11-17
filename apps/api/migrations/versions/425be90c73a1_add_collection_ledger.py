"""Create the payment attempt and confirmed collection ledger."""

import sqlalchemy as sa
from alembic import op

from src.core.studytomy_schema import COLLECTION_HISTORY_GUARDS

revision = "425be90c73a1"
down_revision = "60e9c124ba73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "st_payment_attempt",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("st_order_acceptance.order_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("attempt_reference", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "order_id"], ["st_order.org_id", "st_order.id"],
            name="fk_st_payment_attempt_order", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("org_id", "order_id", "id", name="uq_st_payment_attempt_scope"),
        sa.UniqueConstraint("org_id", "method", "attempt_reference", name="uq_st_payment_attempt_reference"),
        sa.CheckConstraint("method IN ('manual', 'gateway')", name="ck_st_payment_attempt_method"),
    )
    op.create_table(
        "st_manual_collection_evidence",
        sa.Column("attempt_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("external_reference", sa.String(150), nullable=False),
        sa.Column("evidence_reference", sa.String(150), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "order_id", "attempt_id"],
            ["st_payment_attempt.org_id", "st_payment_attempt.order_id", "st_payment_attempt.id"],
            name="fk_st_manual_evidence_attempt", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("org_id", "external_reference", name="uq_st_manual_evidence_external_reference"),
        sa.CheckConstraint("amount_minor > 0", name="ck_st_manual_evidence_amount"),
        sa.CheckConstraint("length(currency) = 3", name="ck_st_manual_evidence_currency"),
        sa.CheckConstraint("length(record_sha256) = 64", name="ck_st_manual_evidence_digest"),
        sa.CheckConstraint("settled_at <= verified_at", name="ck_st_manual_evidence_time"),
    )
    op.create_table(
        "st_collection_remedy",
        sa.Column("attempt_id", sa.BigInteger(), sa.ForeignKey("st_manual_collection_evidence.attempt_id", ondelete="RESTRICT"), primary_key=True, autoincrement=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("opened_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "order_id", "attempt_id"],
            ["st_payment_attempt.org_id", "st_payment_attempt.order_id", "st_payment_attempt.id"],
            name="fk_st_collection_remedy_attempt", ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "reason IN ('amount_or_currency_mismatch', 'additional_collection', 'capacity_or_sale_unavailable')",
            name="ck_st_collection_remedy_reason",
        ),
    )
    op.create_table(
        "st_collection_resolution",
        sa.Column("attempt_id", sa.BigInteger(), sa.ForeignKey("st_collection_remedy.attempt_id", ondelete="RESTRICT"), primary_key=True, autoincrement=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("evidence_reference", sa.String(150), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.Column("resolved_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('returned_externally', 'settlement_not_received')",
            name="ck_st_collection_resolution_outcome",
        ),
        sa.CheckConstraint("length(record_sha256) = 64", name="ck_st_collection_resolution_digest"),
    )
    op.create_table(
        "st_money_movement",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt_id", sa.BigInteger(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("external_reference", sa.String(150), nullable=False),
        sa.Column("evidence_reference", sa.String(150), nullable=False),
        sa.Column("confirmed_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "order_id", "attempt_id"],
            ["st_payment_attempt.org_id", "st_payment_attempt.order_id", "st_payment_attempt.id"],
            name="fk_st_money_movement_attempt", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("attempt_id", name="uq_st_money_movement_attempt"),
        sa.UniqueConstraint("org_id", "external_reference", name="uq_st_money_movement_external_reference"),
        sa.CheckConstraint("amount_minor > 0", name="ck_st_money_movement_amount"),
        sa.CheckConstraint("length(currency) = 3", name="ck_st_money_movement_currency"),
    )
    if op.get_bind().dialect.name == "postgresql":
        for _, _, function_sql, trigger_sql in COLLECTION_HISTORY_GUARDS:
            op.execute(function_sql)
            op.execute(trigger_sql)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT 1 FROM st_payment_attempt LIMIT 1")) is not None:
        raise RuntimeError("Retain payment history during rollback")
    op.drop_table("st_money_movement")
    op.drop_table("st_collection_resolution")
    op.drop_table("st_collection_remedy")
    op.drop_table("st_manual_collection_evidence")
    op.drop_table("st_payment_attempt")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION protect_st_collection_history()")
