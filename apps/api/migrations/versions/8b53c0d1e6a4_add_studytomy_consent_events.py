"""Add immutable, purpose-specific Studytomy consent evidence.

Revision ID: 8b53c0d1e6a4
Revises: f1a76d9c2e40
Create Date: 2026-11-16

Choices remain unusable until their current purpose, authority, notice and
retention policies are approved and the matching authorization is checked.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "8b53c0d1e6a4"
down_revision: Union[str, None] = "f1a76d9c2e40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "st_consent_event",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("event_uuid", sa.String(length=100), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column(
            "learner_profile_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=False,
        ),
        sa.Column("subject_user_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("purpose_code", sa.String(length=32), nullable=False),
        sa.Column("choice", sa.String(length=16), nullable=False),
        sa.Column("withdraws_event_uuid", sa.String(length=100), nullable=True),
        sa.Column("notice_version", sa.String(length=128), nullable=False),
        sa.Column("scope_codes", sa.JSON(), nullable=False),
        sa.Column("policy_version_ids", sa.JSON(), nullable=False),
        sa.Column(
            "provenance",
            sa.String(length=32),
            nullable=False,
        ),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "purpose_code IN ('learning_terms', 'ai_assistance', 'community', 'sharing', 'marketing', 'publicity')",
            name="ck_st_consent_event_purpose",
        ),
        sa.CheckConstraint(
            "choice IN ('accept', 'decline', 'withdraw')",
            name="ck_st_consent_event_choice",
        ),
        sa.CheckConstraint(
            "actor_user_id = subject_user_id",
            name="ck_st_consent_event_self_actor",
        ),
        sa.CheckConstraint(
            "(choice = 'withdraw' AND withdraws_event_uuid IS NOT NULL) OR "
            "(choice <> 'withdraw' AND withdraws_event_uuid IS NULL)",
            name="ck_st_consent_event_withdrawal_reference",
        ),
        sa.CheckConstraint(
            "length(notice_version) BETWEEN 1 AND 128",
            name="ck_st_consent_event_notice",
        ),
        sa.CheckConstraint(
            "json_array_length(scope_codes) > 0",
            name="ck_st_consent_event_scopes",
        ),
        sa.CheckConstraint(
            "json_array_length(policy_version_ids) > 0",
            name="ck_st_consent_event_policies",
        ),
        sa.CheckConstraint(
            "provenance = 'authenticated_self_service'",
            name="ck_st_consent_event_provenance",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["learner_profile_id"], ["st_learner_profile.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["subject_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["withdraws_event_uuid"],
            ["st_consent_event.event_uuid"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_uuid", name="uq_st_consent_event_uuid"),
    )
    op.create_index(
        "ix_st_consent_event_latest",
        "st_consent_event",
        ["org_id", "learner_profile_id", "purpose_code", "created_at", "id"],
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_st_consent_event_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'st_consent_event rows are immutable';
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER st_consent_event_immutable
            BEFORE UPDATE OR DELETE ON st_consent_event
            FOR EACH ROW EXECUTE FUNCTION reject_st_consent_event_mutation()
            """
        )
    elif bind.dialect.name == "sqlite":
        op.execute(
            """
            CREATE TRIGGER st_consent_event_immutable_update
            BEFORE UPDATE ON st_consent_event
            BEGIN SELECT RAISE(ABORT, 'st_consent_event rows are immutable'); END
            """
        )
        op.execute(
            """
            CREATE TRIGGER st_consent_event_immutable_delete
            BEFORE DELETE ON st_consent_event
            BEGIN SELECT RAISE(ABORT, 'st_consent_event rows are immutable'); END
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS st_consent_event_immutable ON st_consent_event")
        op.execute("DROP FUNCTION IF EXISTS reject_st_consent_event_mutation()")
    elif bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS st_consent_event_immutable_update")
        op.execute("DROP TRIGGER IF EXISTS st_consent_event_immutable_delete")
    op.drop_index("ix_st_consent_event_latest", table_name="st_consent_event")
    op.drop_table("st_consent_event")
