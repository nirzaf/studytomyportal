"""Add learner-controlled, time-bounded sharing grants.

Revision ID: f1a76d9c2e40
Revises: e7c4a1b9d603
Create Date: 2026-11-16

The grant is stored separately from guardian relationships. Runtime access is
still denied unless the learner has a current independent-participation decision
and all listed policies remain current.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a76d9c2e40"
down_revision: Union[str, None] = "e7c4a1b9d603"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "st_sharing_grant",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("grant_uuid", sa.String(length=100), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column(
            "learner_profile_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=False,
        ),
        sa.Column("recipient_user_id", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("scope_codes", sa.JSON(), nullable=False),
        sa.Column("policy_version_ids", sa.JSON(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="active"
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("authority_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'withdrawn')", name="ck_st_sharing_grant_status"
        ),
        sa.CheckConstraint("authority_version >= 1", name="ck_st_sharing_grant_version"),
        sa.CheckConstraint("valid_to > valid_from", name="ck_st_sharing_grant_period"),
        sa.CheckConstraint(
            "json_array_length(scope_codes) > 0", name="ck_st_sharing_grant_scopes"
        ),
        sa.CheckConstraint(
            "status <> 'withdrawn' OR withdrawn_at IS NOT NULL",
            name="ck_st_sharing_grant_withdrawal_complete",
        ),
        sa.CheckConstraint(
            "status <> 'active' OR withdrawn_at IS NULL",
            name="ck_st_sharing_grant_active_has_no_withdrawal",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["learner_profile_id"], ["st_learner_profile.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"], ["user.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["user.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_st_sharing_grant_uuid", "st_sharing_grant", ["grant_uuid"], unique=True
    )
    op.create_index(
        "ix_st_sharing_grant_recipient",
        "st_sharing_grant",
        ["org_id", "learner_profile_id", "recipient_user_id", "status", "valid_to"],
    )


def downgrade() -> None:
    op.drop_index("ix_st_sharing_grant_recipient", table_name="st_sharing_grant")
    op.drop_index("ix_st_sharing_grant_uuid", table_name="st_sharing_grant")
    op.drop_table("st_sharing_grant")
