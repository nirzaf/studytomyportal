"""Retain verified managed assessment responses before native retry."""

import sqlalchemy as sa
from alembic import op

from src.core.studytomy_schema import (
    ASSESSMENT_ARCHIVE_POSTGRES_GUARD,
    ASSESSMENT_ARCHIVE_SQLITE_GUARDS,
)

revision = "61e73f9b4c20"
down_revision = "4a9f72c6e801"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "st_assessment_response_archive",
        sa.Column("round_uuid", sa.String(64), primary_key=True),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organization.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "policy_version_id",
            sa.Integer(),
            sa.ForeignKey("st_policy_version.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("responses", sa.JSON(), nullable=False),
        sa.Column("retain_until", sa.DateTime(timezone=True), nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        _, _, function_sql, trigger_sql = ASSESSMENT_ARCHIVE_POSTGRES_GUARD
        op.execute(function_sql)
        op.execute(trigger_sql)
    elif op.get_bind().dialect.name == "sqlite":
        for _, _, trigger_sql in ASSESSMENT_ARCHIVE_SQLITE_GUARDS:
            op.execute(trigger_sql)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "LOCK TABLE st_assessment_response_archive IN ACCESS EXCLUSIVE MODE"
            )
        )
    if connection.execute(
        sa.text("SELECT 1 FROM st_assessment_response_archive LIMIT 1")
    ).first():
        raise RuntimeError(
            "Assessment response archives must be retained before downgrading."
        )
    op.drop_table("st_assessment_response_archive")
    if connection.dialect.name == "postgresql":
        op.execute("DROP FUNCTION reject_st_assessment_archive_mutation()")
