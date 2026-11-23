"""Represent verified guest reporters without Studytomy Portal accounts."""

import sqlalchemy as sa
from alembic import op

revision = "4e7bca98d201"
down_revision = "3f0bd4e972a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("st_service_case") as cases:
        cases.add_column(sa.Column("guest_contact_verified_at", sa.DateTime(timezone=True)))
        cases.alter_column("reporter_user_id", existing_type=sa.Integer(), nullable=True)
        cases.create_check_constraint(
            "ck_st_service_case_reporter_kind",
            "(reporter_user_id IS NOT NULL AND guest_contact_verified_at IS NULL) OR "
            "(reporter_user_id IS NULL AND guest_contact_verified_at IS NOT NULL "
            "AND category = 'support' AND support_topic IS NOT NULL)",
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE st_service_case IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT 1 FROM st_service_case WHERE reporter_user_id IS NULL LIMIT 1")).first():
        raise RuntimeError("Retained guest support cases block downgrade.")
    with op.batch_alter_table("st_service_case") as cases:
        cases.drop_constraint("ck_st_service_case_reporter_kind", type_="check")
        cases.alter_column("reporter_user_id", existing_type=sa.Integer(), nullable=False)
        cases.drop_column("guest_contact_verified_at")
