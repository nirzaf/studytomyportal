"""Bind approved support topics to service cases."""

import sqlalchemy as sa
from alembic import op

revision = "8ab143f5c096"
down_revision = "72c1a8f0d4e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("st_service_case") as cases:
        cases.add_column(sa.Column("support_topic", sa.String(100)))
        cases.create_check_constraint(
            "ck_st_service_case_support_topic",
            "support_topic IS NULL OR (category = 'support' AND length(support_topic) BETWEEN 1 AND 100)",
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE st_service_case IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT 1 FROM st_service_case WHERE support_topic IS NOT NULL LIMIT 1")).first():
        raise RuntimeError("Retained support case topics block downgrade.")
    with op.batch_alter_table("st_service_case") as cases:
        cases.drop_constraint("ck_st_service_case_support_topic", type_="check")
        cases.drop_column("support_topic")
