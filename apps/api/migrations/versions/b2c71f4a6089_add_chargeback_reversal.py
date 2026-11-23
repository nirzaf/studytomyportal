"""Retain verified full chargeback reversals."""

import sqlalchemy as sa
from alembic import op

from src.core.studytomy_schema import CHARGEBACK_REVERSAL_GUARD

revision = "b2c71f4a6089"
down_revision = "e2a9c571d643"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("st_chargeback_adjustment") as batch:
        batch.create_unique_constraint("uq_st_chargeback_org_id", ["org_id", "id"])
    op.create_table(
        "st_chargeback_reversal",
        sa.Column("adjustment_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("external_reference", sa.String(150), nullable=False),
        sa.Column("case_reference", sa.String(150), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("remedy_policy_id", sa.Integer(), sa.ForeignKey("st_policy_version.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("finance_policy_id", sa.Integer(), sa.ForeignKey("st_policy_version.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "adjustment_id"], ["st_chargeback_adjustment.org_id", "st_chargeback_adjustment.id"],
            name="fk_st_chargeback_reversal_adjustment", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("org_id", "external_reference", name="uq_st_chargeback_reversal_reference"),
        sa.CheckConstraint("length(record_sha256) = 64", name="ck_st_chargeback_reversal_digest"),
        sa.CheckConstraint("effective_at <= verified_at", name="ck_st_chargeback_reversal_time"),
    )
    if op.get_bind().dialect.name == "postgresql":
        _, _, function_sql, trigger_sql = CHARGEBACK_REVERSAL_GUARD
        op.execute(function_sql)
        op.execute(trigger_sql)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT 1 FROM st_chargeback_reversal LIMIT 1")) is not None:
        raise RuntimeError("Retain chargeback reversal history during rollback")
    op.drop_table("st_chargeback_reversal")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION protect_st_chargeback_reversal()")
    with op.batch_alter_table("st_chargeback_adjustment") as batch:
        batch.drop_constraint("uq_st_chargeback_org_id", type_="unique")
