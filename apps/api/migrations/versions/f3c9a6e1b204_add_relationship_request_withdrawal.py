"""Add a durable terminal withdrawal for pending guardian requests."""

import sqlalchemy as sa
from alembic import op


revision = "f3c9a6e1b204"
down_revision = "c2d8e91f4a70"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "st_relationship",
        sa.Column("withdrawn_by_user_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "st_relationship",
        sa.Column("withdrawal_evidence_reference", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "st_relationship",
        sa.Column("withdrawal_reason_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "st_relationship",
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_st_relationship_withdrawn_by_user_id_user",
        "st_relationship",
        "user",
        ["withdrawn_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.drop_constraint(
        "ck_st_relationship_status", "st_relationship", type_="check"
    )
    op.drop_constraint(
        "ck_st_relationship_decision_complete", "st_relationship", type_="check"
    )
    op.drop_constraint(
        "ck_st_relationship_pending_has_no_decision",
        "st_relationship",
        type_="check",
    )
    op.create_check_constraint(
        "ck_st_relationship_status",
        "st_relationship",
        "status IN ('pending', 'accepted', 'denied', 'withdrawn', 'restricted', 'disputed', 'revoked', 'expired')",
    )
    op.create_check_constraint(
        "ck_st_relationship_decision_complete",
        "st_relationship",
        "status IN ('pending', 'withdrawn') OR (decided_by_user_id IS NOT NULL AND decision_owner_assignment_id IS NOT NULL AND decided_at IS NOT NULL AND decision_evidence_reference IS NOT NULL AND decision_reason_code IS NOT NULL AND decision_policy_version_ids IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_st_relationship_pending_has_no_decision",
        "st_relationship",
        "status NOT IN ('pending', 'withdrawn') OR (decided_by_user_id IS NULL AND decision_owner_assignment_id IS NULL AND decided_at IS NULL AND decision_evidence_reference IS NULL AND decision_reason_code IS NULL AND decision_policy_version_ids IS NULL)",
    )
    op.create_check_constraint(
        "ck_st_relationship_withdrawal_complete",
        "st_relationship",
        "status <> 'withdrawn' OR (withdrawn_by_user_id IS NOT NULL AND withdrawal_evidence_reference IS NOT NULL AND withdrawal_reason_code IS NOT NULL AND withdrawn_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_st_relationship_nonwithdrawn_has_no_withdrawal",
        "st_relationship",
        "status = 'withdrawn' OR (withdrawn_by_user_id IS NULL AND withdrawal_evidence_reference IS NULL AND withdrawal_reason_code IS NULL AND withdrawn_at IS NULL)",
    )


def downgrade() -> None:
    raise RuntimeError(
        "A withdrawal is an immutable terminal relationship decision; downgrade must not reopen requests."
    )
