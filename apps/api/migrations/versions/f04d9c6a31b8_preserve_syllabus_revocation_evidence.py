"""Preserve approval evidence when a source or mapping is revoked."""

from alembic import op
import sqlalchemy as sa


revision = "f04d9c6a31b8"
down_revision = "e3c4b1d928af"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("st_syllabus_source_version", "st_syllabus_concept_mapping"):
        op.add_column(
            table,
            sa.Column(
                "revoked_by_user_id",
                sa.Integer(),
                sa.ForeignKey("user.id", ondelete="RESTRICT"),
                nullable=True,
            ),
        )
        op.add_column(table, sa.Column("revocation_evidence", sa.String(length=500), nullable=True))
        op.add_column(table, sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text(
                "LOCK TABLE st_syllabus_source_version, st_syllabus_concept_mapping "
                "IN ACCESS EXCLUSIVE MODE"
            )
        )
    tables = ("st_syllabus_source_version", "st_syllabus_concept_mapping")
    if any(
        connection.execute(
            sa.text(
                f"SELECT 1 FROM {table} WHERE revoked_by_user_id IS NOT NULL "
                "OR revocation_evidence IS NOT NULL OR revoked_at IS NOT NULL LIMIT 1"
            )
        ).first()
        for table in tables
    ):
        raise RuntimeError("Retained syllabus revocation evidence blocks downgrade.")
    for table in ("st_syllabus_concept_mapping", "st_syllabus_source_version"):
        op.drop_column(table, "revoked_at")
        op.drop_column(table, "revocation_evidence")
        op.drop_column(table, "revoked_by_user_id")
