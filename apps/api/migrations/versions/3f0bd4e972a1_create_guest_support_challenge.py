"""Add short-lived guest support verification state."""

import sqlalchemy as sa
from alembic import op

revision = "3f0bd4e972a1"
down_revision = "8ab143f5c096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "st_guest_support_challenge",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("org_id", sa.Integer, sa.ForeignKey("organization.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_digest", sa.String(64), nullable=False),
        sa.Column("contact_ciphertext", sa.Text, nullable=False),
        sa.Column("topic", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("case_uuid", sa.String(36)),
        sa.ForeignKeyConstraint(
            ["org_id", "case_uuid"], ["st_service_case.org_id", "st_service_case.case_uuid"],
            name="fk_st_guest_support_challenge_org_case", ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("org_id", "token_digest", name="uq_st_guest_support_challenge_org_token"),
        sa.CheckConstraint("length(token_digest) = 64", name="ck_st_guest_support_challenge_digest"),
        sa.CheckConstraint("length(contact_ciphertext) > 0", name="ck_st_guest_support_challenge_contact"),
        sa.CheckConstraint("length(topic) BETWEEN 1 AND 100", name="ck_st_guest_support_challenge_topic"),
        sa.CheckConstraint(
            "created_at < expires_at AND "
            "((consumed_at IS NULL AND case_uuid IS NULL) OR "
            "(consumed_at IS NOT NULL AND case_uuid IS NOT NULL "
            "AND consumed_at >= created_at AND consumed_at < expires_at))",
            name="ck_st_guest_support_challenge_window",
        ),
    )
    op.create_index("ix_st_guest_support_challenge_expiry", "st_guest_support_challenge", ["expires_at"])


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE st_guest_support_challenge IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT 1 FROM st_guest_support_challenge LIMIT 1")).first():
        raise RuntimeError("Retained guest support challenges block downgrade.")
    op.drop_table("st_guest_support_challenge")
