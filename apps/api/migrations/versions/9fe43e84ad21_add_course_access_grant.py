"""Persist independent access grants pinned to reviewed releases.

Revision ID: 9fe43e84ad21
Revises: b043fde62c98
"""

import sqlalchemy as sa
from alembic import op

from src.core.studytomy_schema import COURSE_GRANT_GUARD

revision = "9fe43e84ad21"
down_revision = "b043fde62c98"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("st_course_release") as batch:
        batch.create_unique_constraint("uq_st_course_release_scope", ["org_id", "course_uuid", "id"])
    op.create_table(
        "st_course_access_grant",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("learner_profile_id", sa.BigInteger(), nullable=False),
        sa.Column("course_uuid", sa.String(255), nullable=False),
        sa.Column("release_id", sa.BigInteger(), nullable=False),
        sa.Column("source_kind", sa.String(24), nullable=False),
        sa.Column("source_uuid", sa.String(100), nullable=False),
        sa.Column("start_basis", sa.String(24), nullable=False),
        sa.Column("duration_days", sa.Integer()),
        sa.Column("starts_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "learner_profile_id"], ["st_learner_profile.org_id", "st_learner_profile.id"],
            ondelete="RESTRICT", name="fk_st_course_grant_learner",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "course_uuid", "release_id"],
            ["st_course_release.org_id", "st_course_release.course_uuid", "st_course_release.id"],
            ondelete="RESTRICT", name="fk_st_course_grant_release",
        ),
        sa.UniqueConstraint(
            "org_id", "learner_profile_id", "course_uuid", "source_kind", "source_uuid",
            name="uq_st_course_grant_source",
        ),
        sa.CheckConstraint(
            "source_kind IN ('order_line', 'membership_cycle', 'free', 'sponsor', 'provisional')",
            name="ck_st_course_grant_source_kind",
        ),
        sa.CheckConstraint(
            "length(source_uuid) BETWEEN 1 AND 100", name="ck_st_course_grant_source_uuid",
        ),
        sa.CheckConstraint(
            "(start_basis IN ('after_purchase', 'fixed_period') AND starts_at IS NOT NULL "
            "AND ends_at IS NOT NULL AND duration_days IS NULL) OR "
            "(start_basis = 'after_first_access' AND duration_days BETWEEN 1 AND 36500 "
            "AND duration_days IS NOT NULL AND "
            "((starts_at IS NULL AND ends_at IS NULL) OR (starts_at IS NOT NULL AND ends_at IS NOT NULL)))",
            name="ck_st_course_grant_clock",
        ),
        sa.CheckConstraint("ends_at > starts_at", name="ck_st_course_grant_window"),
        sa.CheckConstraint(
            "source_kind <> 'provisional' OR start_basis = 'fixed_period'",
            name="ck_st_course_grant_provisional_window",
        ),
    )
    if op.get_bind().dialect.name == "postgresql":
        _, _, function_sql, trigger_sql = COURSE_GRANT_GUARD
        op.execute(function_sql)
        op.execute(trigger_sql)


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT 1 FROM st_course_access_grant LIMIT 1")) is not None:
        raise RuntimeError("Retain course access grant history during rollback")
    op.drop_table("st_course_access_grant")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION protect_st_course_access_grant()")
    with op.batch_alter_table("st_course_release") as batch:
        batch.drop_constraint("uq_st_course_release_scope", type_="unique")
