"""Add durable tutor session authority.

Revision ID: 37e0adb59ca1
Revises: d6b2beaf0c43
"""

import sqlalchemy as sa
from alembic import op

revision = "37e0adb59ca1"
down_revision = "d6b2beaf0c43"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "st_tutor_session",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True
        ),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organization.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uuid", sa.String(64), nullable=False),
        sa.Column("learner_profile_id", sa.BigInteger(), nullable=False),
        sa.Column("curriculum_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("concept_id", sa.BigInteger(), nullable=False),
        sa.Column("activity_uuid", sa.String(89), nullable=False),
        sa.Column("activity_digest", sa.String(64), nullable=False),
        sa.Column("course_uuid", sa.String(255), nullable=False),
        sa.Column("practice_attempt_id", sa.BigInteger()),
        sa.Column("eligibility_decision_id", sa.BigInteger(), nullable=False),
        sa.Column("policy_version_ids", sa.JSON(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ("org_id", "learner_profile_id"),
            ("st_learner_profile.org_id", "st_learner_profile.id"),
            name="fk_st_tutor_learner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ("org_id", "curriculum_revision_id", "concept_id"),
            ("st_concept.org_id", "st_concept.curriculum_revision_id", "st_concept.id"),
            name="fk_st_tutor_concept",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            (
                "org_id",
                "practice_attempt_id",
                "learner_profile_id",
                "curriculum_revision_id",
            ),
            (
                "st_learning_attempt.org_id",
                "st_learning_attempt.id",
                "st_learning_attempt.learner_profile_id",
                "st_learning_attempt.curriculum_revision_id",
            ),
            name="fk_st_tutor_practice",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("uuid", name="uq_st_tutor_session_uuid"),
        sa.CheckConstraint(
            "version >= 1 AND step >= 0 AND length(activity_digest) = 64 AND mode = 'guided' AND "
            "(closed_at IS NULL OR closed_at >= created_at)",
            name="ck_st_tutor_state",
        ),
    )


def downgrade() -> None:
    op.drop_table("st_tutor_session")
