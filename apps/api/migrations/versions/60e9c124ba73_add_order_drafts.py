"""Create private order drafts with tenant-bound named allocations."""

import sqlalchemy as sa
from alembic import op

from src.core.studytomy_schema import ORDER_HISTORY_GUARDS

revision = "60e9c124ba73"
down_revision = "71af29d0c8e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("st_offer") as batch:
        batch.create_unique_constraint("uq_st_offer_org_id", ["org_id", "id"])
    op.create_table(
        "st_order",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("purchaser_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("customer_ref", sa.String(100), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True)),
        sa.Column("confirmation_sha256", sa.String(64)),
        sa.UniqueConstraint("org_id", "id", name="uq_st_order_org_id"),
        sa.UniqueConstraint("org_id", "customer_ref", name="uq_st_order_customer_ref"),
        sa.CheckConstraint("total_minor >= 0", name="ck_st_order_total"),
        sa.CheckConstraint("length(currency) = 3", name="ck_st_order_currency"),
        sa.CheckConstraint(
            "(confirmation_sha256 IS NULL AND sealed_at IS NULL) OR "
            "(confirmation_sha256 IS NOT NULL AND length(confirmation_sha256) = 64)",
            name="ck_st_order_confirmation_digest",
        ),
    )
    op.create_table(
        "st_order_acceptance",
        sa.Column("order_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("purchaser_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("confirmation_sha256", sa.String(64), nullable=False),
        sa.Column("policy_binding", sa.JSON(), nullable=False),
        sa.Column("acknowledged_overlap", sa.Boolean(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "order_id"], ["st_order.org_id", "st_order.id"],
            name="fk_st_order_acceptance_order", ondelete="RESTRICT",
        ),
        sa.CheckConstraint("length(confirmation_sha256) = 64", name="ck_st_order_acceptance_digest"),
    )
    op.create_table(
        "st_order_line",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.BigInteger(), nullable=False),
        sa.Column("learner_profile_id", sa.BigInteger(), nullable=False),
        sa.Column("offer_id", sa.BigInteger(), nullable=False),
        sa.Column("offer_version_no", sa.Integer(), nullable=False),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("terms_snapshot", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "order_id"], ["st_order.org_id", "st_order.id"],
            name="fk_st_order_line_order", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "learner_profile_id"], ["st_learner_profile.org_id", "st_learner_profile.id"],
            name="fk_st_order_line_learner", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "offer_id"], ["st_offer.org_id", "st_offer.id"],
            name="fk_st_order_line_offer", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["offer_id", "offer_version_no"], ["st_offer_version.offer_id", "st_offer_version.version_no"],
            name="fk_st_order_line_version", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("order_id", "learner_profile_id", "offer_id", name="uq_st_order_line_allocation"),
        sa.CheckConstraint("total_minor >= 0", name="ck_st_order_line_total"),
    )
    if op.get_bind().dialect.name == "postgresql":
        for _, _, function_sql, trigger_sql in ORDER_HISTORY_GUARDS:
            op.execute(function_sql)
            op.execute(trigger_sql)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT 1 FROM st_order LIMIT 1")) is not None:
        raise RuntimeError("Retain order history during rollback")
    op.drop_table("st_order_line")
    op.drop_table("st_order_acceptance")
    op.drop_table("st_order")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION protect_st_order_history()")
    with op.batch_alter_table("st_offer") as batch:
        batch.drop_constraint("uq_st_offer_org_id", type_="unique")
