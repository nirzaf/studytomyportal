"""Retain private PDF source versions for managed course drafting."""

from alembic import op
import sqlalchemy as sa

revision = "6ab47ca092df"
down_revision = "4e7bca98d201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "st_draft_material",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_uuid", sa.String(36), nullable=False, unique=True),
        sa.Column("course_id", sa.Integer, sa.ForeignKey("course.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("activity_id", sa.Integer, sa.ForeignKey("activity.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Integer, sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("label", sa.String(160), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("original_pdf", sa.LargeBinary, nullable=False),
        sa.Column("pages", sa.JSON, nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("course_id", "activity_id", "sha256", name="uq_st_draft_material_lesson_digest"),
    )
    op.create_table(
        "st_grounded_lesson_evidence",
        sa.Column("activity_id", sa.Integer, sa.ForeignKey("activity.id", ondelete="RESTRICT"), primary_key=True),
        sa.Column("activity_version", sa.Integer, primary_key=True),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("citations", sa.JSON, nullable=False),
    )
    op.create_table(
        "st_grounded_quiz_evidence",
        sa.Column("task_uuid", sa.String(100), primary_key=True),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("citations", sa.JSON, nullable=False),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text(
            "LOCK TABLE st_draft_material, st_grounded_lesson_evidence, st_grounded_quiz_evidence IN ACCESS EXCLUSIVE MODE"
        ))
    if any(connection.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first()
           for table in ("st_draft_material", "st_grounded_lesson_evidence", "st_grounded_quiz_evidence")):
        raise RuntimeError("Retained draft materials block downgrade.")
    op.drop_table("st_grounded_quiz_evidence")
    op.drop_table("st_grounded_lesson_evidence")
    op.drop_table("st_draft_material")
