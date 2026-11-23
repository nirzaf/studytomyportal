"""Retain finite offer capacity holds."""

import sqlalchemy as sa
from alembic import op

from src.core.studytomy_schema import CAPACITY_HOLD_GUARD

revision = "71af29d0c8e4"
down_revision = "9fe43e84ad21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "st_capacity_hold",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("offer_id", sa.BigInteger(), sa.ForeignKey("st_offer.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("request_uuid", sa.String(100), nullable=False),
        sa.Column("places", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("offer_id", "request_uuid", name="uq_st_capacity_hold_request"),
        sa.CheckConstraint("places > 0", name="ck_st_capacity_hold_places"),
        sa.CheckConstraint("expires_at > created_at", name="ck_st_capacity_hold_expiry"),
        sa.CheckConstraint(
            "consumed_at IS NULL OR (consumed_at >= created_at AND consumed_at < expires_at)",
            name="ck_st_capacity_hold_consumed",
        ),
    )
    if op.get_bind().dialect.name == "postgresql":
        _, _, function_sql, trigger_sql = CAPACITY_HOLD_GUARD
        op.execute(function_sql)
        op.execute(trigger_sql)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT 1 FROM st_capacity_hold LIMIT 1")) is not None:
        raise RuntimeError("Retain offer capacity history during rollback")
    op.drop_table("st_capacity_hold")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION protect_st_capacity_hold()")
