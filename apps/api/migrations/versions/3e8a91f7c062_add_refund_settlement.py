"""Retain verified external refund settlement evidence."""

import sqlalchemy as sa
from alembic import op

from src.core.studytomy_schema import REFUND_SETTLEMENT_GUARD

revision = "3e8a91f7c062"
down_revision = "c0f63d7b29a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("st_refund_reservation") as batch:
        batch.create_unique_constraint("uq_st_refund_reservation_org_id", ["org_id", "id"])
    op.create_table(
        "st_refund_settlement",
        sa.Column("reservation_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("external_reference", sa.String(150), nullable=False),
        sa.Column("evidence_reference", sa.String(150), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("remedy_policy_id", sa.Integer(), sa.ForeignKey("st_policy_version.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("finance_policy_id", sa.Integer(), sa.ForeignKey("st_policy_version.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "reservation_id"], ["st_refund_reservation.org_id", "st_refund_reservation.id"],
            name="fk_st_refund_settlement_reservation", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("org_id", "external_reference", name="uq_st_refund_settlement_external_reference"),
        sa.CheckConstraint("length(external_reference) > 0", name="ck_st_refund_settlement_external_reference"),
        sa.CheckConstraint("length(evidence_reference) > 0", name="ck_st_refund_settlement_evidence_reference"),
        sa.CheckConstraint("length(record_sha256) = 64", name="ck_st_refund_settlement_digest"),
        sa.CheckConstraint("settled_at <= verified_at", name="ck_st_refund_settlement_time"),
        sa.CheckConstraint("remedy_policy_id <> finance_policy_id", name="ck_st_refund_settlement_policies"),
    )
    if op.get_bind().dialect.name == "postgresql":
        _, _, function_sql, trigger_sql = REFUND_SETTLEMENT_GUARD
        op.execute(function_sql)
        op.execute(trigger_sql)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT 1 FROM st_refund_settlement LIMIT 1")) is not None:
        raise RuntimeError("Retain refund settlement history during rollback")
    op.drop_table("st_refund_settlement")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION protect_st_refund_settlement()")
    with op.batch_alter_table("st_refund_reservation") as batch:
        batch.drop_constraint("uq_st_refund_reservation_org_id", type_="unique")
