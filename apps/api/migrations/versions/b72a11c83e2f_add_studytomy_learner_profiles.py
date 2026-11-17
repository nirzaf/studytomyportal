"""Add individual Studytomy learner profiles and eligibility history.

Revision ID: b72a11c83e2f
Revises: a1d9f4c8b732
Create Date: 2026-11-15

Profiles reference existing LearnHouse users. Eligibility decisions are
append-only, policy-bound history; they do not grant course access.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b72a11c83e2f"
down_revision: Union[str, None] = "a1d9f4c8b732"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "st_learner_profile",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("learner_uuid", sa.String(length=100), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("control_mode", sa.String(length=16), nullable=False),
        sa.Column("time_zone", sa.String(length=64), nullable=True),
        sa.Column("access_version", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("access_version >= 1", name="ck_st_learner_profile_access_version"),
        sa.CheckConstraint(
            "control_mode IN ('unresolved', 'dependent', 'independent')",
            name="ck_st_learner_profile_control_mode",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'suspended', 'closed')",
            name="ck_st_learner_profile_status",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "user_id", name="uq_st_learner_profile_org_user"),
    )
    op.create_index("ix_st_learner_profile_uuid", "st_learner_profile", ["learner_uuid"], unique=True)
    op.create_index("ix_st_learner_profile_org_status", "st_learner_profile", ["org_id", "status"])
    op.create_index("ix_st_learner_profile_user_id", "st_learner_profile", ["user_id"])

    op.create_table(
        "st_eligibility_decision",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("decision_uuid", sa.String(length=100), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("learner_profile_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("eligibility_kind", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("policy_version_ids", sa.JSON(), nullable=False),
        sa.Column("evidence_reference", sa.String(length=500), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer_user_id", sa.Integer(), nullable=False),
        sa.Column("owner_assignment_id", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "eligibility_kind IN ('learning', 'independent_participation', 'purchase')",
            name="ck_st_eligibility_decision_kind",
        ),
        sa.CheckConstraint(
            "outcome IN ('eligible', 'ineligible', 'review_required')",
            name="ck_st_eligibility_decision_outcome",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_st_eligibility_decision_effective_range",
        ),
        sa.CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id",
            name="ck_st_eligibility_decision_not_self_superseding",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["learner_profile_id"], ["st_learner_profile.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["reviewer_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["owner_assignment_id"], ["st_owner_assignment.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"], ["st_eligibility_decision.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("supersedes_id", name="uq_st_eligibility_decision_successor"),
    )
    op.create_index(
        "ix_st_eligibility_decision_uuid",
        "st_eligibility_decision",
        ["decision_uuid"],
        unique=True,
    )
    op.create_index(
        "ix_st_eligibility_decision_profile_kind",
        "st_eligibility_decision",
        ["org_id", "learner_profile_id", "eligibility_kind", "created_at"],
    )
    op.create_index(
        "uq_st_eligibility_decision_root",
        "st_eligibility_decision",
        ["org_id", "learner_profile_id", "eligibility_kind"],
        unique=True,
        postgresql_where=sa.text("supersedes_id IS NULL"),
        sqlite_where=sa.text("supersedes_id IS NULL"),
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_st_eligibility_decision_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'st_eligibility_decision rows are immutable';
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER st_eligibility_decision_immutable
            BEFORE UPDATE OR DELETE ON st_eligibility_decision
            FOR EACH ROW EXECUTE FUNCTION reject_st_eligibility_decision_mutation()
            """
        )
    elif bind.dialect.name == "sqlite":
        op.execute(
            """
            CREATE TRIGGER st_eligibility_decision_immutable_update
            BEFORE UPDATE ON st_eligibility_decision
            BEGIN SELECT RAISE(ABORT, 'st_eligibility_decision rows are immutable'); END
            """
        )
        op.execute(
            """
            CREATE TRIGGER st_eligibility_decision_immutable_delete
            BEFORE DELETE ON st_eligibility_decision
            BEGIN SELECT RAISE(ABORT, 'st_eligibility_decision rows are immutable'); END
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS st_eligibility_decision_immutable ON st_eligibility_decision")
        op.execute("DROP FUNCTION IF EXISTS reject_st_eligibility_decision_mutation()")
    elif bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS st_eligibility_decision_immutable_update")
        op.execute("DROP TRIGGER IF EXISTS st_eligibility_decision_immutable_delete")
    op.drop_index("uq_st_eligibility_decision_root", table_name="st_eligibility_decision")
    op.drop_index("ix_st_eligibility_decision_profile_kind", table_name="st_eligibility_decision")
    op.drop_index("ix_st_eligibility_decision_uuid", table_name="st_eligibility_decision")
    op.drop_table("st_eligibility_decision")
    op.drop_index("ix_st_learner_profile_user_id", table_name="st_learner_profile")
    op.drop_index("ix_st_learner_profile_org_status", table_name="st_learner_profile")
    op.drop_index("ix_st_learner_profile_uuid", table_name="st_learner_profile")
    op.drop_table("st_learner_profile")
