"""Add pending, policy-bound guardian relationship records.

Revision ID: e7c4a1b9d603
Revises: d4a7c9e2b1f0
Create Date: 2026-11-16

No relationship grants access by default. Only an assigned review decision can
populate approved scopes and validity, and the service rechecks active policy
versions before resolving a grant.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7c4a1b9d603"
down_revision: Union[str, None] = "d4a7c9e2b1f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "st_relationship",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=False,
        ),
        sa.Column("relationship_uuid", sa.String(length=100), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column(
            "learner_profile_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            nullable=False,
        ),
        sa.Column("adult_user_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=False),
        sa.Column("relationship_type", sa.String(length=24), nullable=False),
        sa.Column("requested_scopes", sa.JSON(), nullable=False),
        sa.Column("approved_scopes", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("request_evidence_reference", sa.String(length=500), nullable=False),
        sa.Column("request_policy_version_ids", sa.JSON(), nullable=False),
        sa.Column("decision_policy_version_ids", sa.JSON(), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_by_user_id", sa.Integer(), nullable=True),
        sa.Column("decision_owner_assignment_id", sa.Integer(), nullable=True),
        sa.Column("decision_evidence_reference", sa.String(length=500), nullable=True),
        sa.Column("decision_reason_code", sa.String(length=64), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("authority_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "relationship_type IN ('guardian')",
            name="ck_st_relationship_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'denied', 'restricted', 'disputed', 'revoked', 'expired')",
            name="ck_st_relationship_status",
        ),
        sa.CheckConstraint(
            "authority_version >= 1", name="ck_st_relationship_authority_version"
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from",
            name="ck_st_relationship_validity_range",
        ),
        sa.CheckConstraint(
            "status <> 'accepted' OR (valid_from IS NOT NULL AND valid_to IS NOT NULL AND json_array_length(approved_scopes) > 0)",
            name="ck_st_relationship_accepted_has_scope_and_period",
        ),
        sa.CheckConstraint(
            "status = 'accepted' OR json_array_length(approved_scopes) = 0",
            name="ck_st_relationship_nonaccepted_has_no_scope",
        ),
        sa.CheckConstraint(
            "status = 'pending' OR (decided_by_user_id IS NOT NULL AND decision_owner_assignment_id IS NOT NULL AND decided_at IS NOT NULL AND decision_evidence_reference IS NOT NULL AND decision_reason_code IS NOT NULL AND decision_policy_version_ids IS NOT NULL)",
            name="ck_st_relationship_decision_complete",
        ),
        sa.CheckConstraint(
            "status <> 'pending' OR (decided_by_user_id IS NULL AND decision_owner_assignment_id IS NULL AND decided_at IS NULL AND decision_evidence_reference IS NULL AND decision_reason_code IS NULL AND decision_policy_version_ids IS NULL)",
            name="ck_st_relationship_pending_has_no_decision",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organization.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["learner_profile_id"], ["st_learner_profile.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["adult_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["user.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_user_id"], ["user.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["decision_owner_assignment_id"],
            ["st_owner_assignment.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_st_relationship_uuid", "st_relationship", ["relationship_uuid"], unique=True
    )
    op.create_index(
        "ix_st_relationship_current",
        "st_relationship",
        ["org_id", "learner_profile_id", "adult_user_id", "status", "valid_to"],
    )
    op.create_index(
        "ix_st_relationship_review",
        "st_relationship",
        ["org_id", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_st_relationship_review", table_name="st_relationship")
    op.drop_index("ix_st_relationship_current", table_name="st_relationship")
    op.drop_index("ix_st_relationship_uuid", table_name="st_relationship")
    op.drop_table("st_relationship")
