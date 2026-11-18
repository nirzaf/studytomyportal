"""Bind adaptive attempts and tutor sessions to exact managed releases."""

import sqlalchemy as sa
from alembic import op

revision = "b91d2f6a08c3"
down_revision = "a78f2c9d1e64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_st_course_release_course_uuid",
        "st_course_release",
        ["org_id", "course_uuid", "uuid"],
    )

    op.add_column(
        "st_learning_attempt",
        sa.Column("source_release_uuid", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "st_learning_attempt",
        sa.Column("source_binding_digest", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_st_attempt_release_source",
        "st_learning_attempt",
        "st_course_release",
        ["org_id", "source_course_uuid", "source_release_uuid"],
        ["org_id", "course_uuid", "uuid"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_st_attempt_release_binding",
        "st_learning_attempt",
        "(source_release_uuid IS NULL AND source_binding_digest IS NULL) OR "
        "(source_release_uuid IS NOT NULL AND source_binding_digest IS NOT NULL "
        "AND length(source_binding_digest) = 64)",
    )

    op.add_column(
        "st_tutor_session",
        sa.Column("source_release_uuid", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "st_tutor_session",
        sa.Column("source_binding_digest", sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        "fk_st_tutor_release_source",
        "st_tutor_session",
        "st_course_release",
        ["org_id", "course_uuid", "source_release_uuid"],
        ["org_id", "course_uuid", "uuid"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_st_tutor_release_binding",
        "st_tutor_session",
        "(source_release_uuid IS NULL AND source_binding_digest IS NULL) OR "
        "(source_release_uuid IS NOT NULL AND source_binding_digest IS NOT NULL "
        "AND length(source_binding_digest) = 64)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_st_tutor_release_binding", "st_tutor_session", type_="check"
    )
    op.drop_constraint(
        "fk_st_tutor_release_source", "st_tutor_session", type_="foreignkey"
    )
    op.drop_column("st_tutor_session", "source_binding_digest")
    op.drop_column("st_tutor_session", "source_release_uuid")

    op.drop_constraint(
        "ck_st_attempt_release_binding", "st_learning_attempt", type_="check"
    )
    op.drop_constraint(
        "fk_st_attempt_release_source", "st_learning_attempt", type_="foreignkey"
    )
    op.drop_column("st_learning_attempt", "source_binding_digest")
    op.drop_column("st_learning_attempt", "source_release_uuid")

    op.drop_constraint(
        "uq_st_course_release_course_uuid", "st_course_release", type_="unique"
    )
