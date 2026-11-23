"""Add explicit Studytomy policy versions, owner assignments, and approvals.

Revision ID: a1d9f4c8b732
Revises: f5a8b3d0c2e4
Create Date: 2026-11-15

All new rows are additive and retain their referenced organization and user
records. Policy approval remains an explicit, scoped human action.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1d9f4c8b732"
down_revision: Union[str, None] = "f5a8b3d0c2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


POLICY_KEYS = ", ".join(f"'{value}'" for value in (f"D-{number:02d}" for number in range(1, 21)))
OWNER_FUNCTIONS = "'business', 'education', 'safeguarding', 'family_services', 'support', 'commercial', 'service', 'privacy'"


def upgrade() -> None:
    op.create_table(
        "st_policy_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("policy_uuid", sa.String(length=100), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("policy_key", sa.String(length=5), nullable=False),
        sa.Column("scope_kind", sa.String(length=32), nullable=False),
        sa.Column("scope_uuid", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("body", sa.JSON(), nullable=False),
        sa.Column("evidence_reference", sa.String(length=500), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("approved_by_user_id", sa.Integer(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersession_evidence_reference", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_st_policy_version_positive"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_st_policy_version_effective_range",
        ),
        sa.CheckConstraint("scope_kind = 'organization'", name="ck_st_policy_version_scope_kind"),
        sa.CheckConstraint(f"policy_key IN ({POLICY_KEYS})", name="ck_st_policy_version_policy_key"),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'superseded', 'withdrawn')",
            name="ck_st_policy_version_status",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supersedes_id"], ["st_policy_version.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "policy_key", "scope_kind", "scope_uuid", "version",
            name="uq_st_policy_version_sequence",
        ),
    )
    op.create_index("ix_st_policy_version_policy_uuid", "st_policy_version", ["policy_uuid"], unique=True)
    op.create_index("ix_st_policy_version_org_id", "st_policy_version", ["org_id"])
    op.create_index(
        "ix_st_policy_version_scope_status",
        "st_policy_version",
        ["org_id", "policy_key", "scope_uuid", "status"],
    )

    op.create_table(
        "st_owner_assignment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("owner_uuid", sa.String(length=100), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("responsibility", sa.String(length=32), nullable=False),
        sa.Column("scope_kind", sa.String(length=32), nullable=False),
        sa.Column("scope_uuid", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("evidence_reference", sa.String(length=500), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("financial_limit_minor", sa.BigInteger(), nullable=True),
        sa.Column("financial_limit_currency", sa.String(length=3), nullable=True),
        sa.Column("delegation_evidence_reference", sa.String(length=500), nullable=True),
        sa.Column("delegation_policy_version_id", sa.Integer(), nullable=True),
        sa.Column("assigned_by_user_id", sa.Integer(), nullable=False),
        sa.Column("ended_by_user_id", sa.Integer(), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_evidence_reference", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope_kind = 'organization'", name="ck_st_owner_assignment_scope_kind"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_st_owner_assignment_effective_range",
        ),
        sa.CheckConstraint(f"responsibility IN ({OWNER_FUNCTIONS})", name="ck_st_owner_assignment_responsibility"),
        sa.CheckConstraint("status IN ('active', 'ended')", name="ck_st_owner_assignment_status"),
        sa.CheckConstraint(
            "financial_limit_minor IS NULL OR financial_limit_minor >= 0",
            name="ck_st_owner_assignment_financial_limit_nonnegative",
        ),
        sa.CheckConstraint(
            "(financial_limit_minor IS NULL AND financial_limit_currency IS NULL "
            "AND delegation_evidence_reference IS NULL AND delegation_policy_version_id IS NULL) "
            "OR (financial_limit_minor IS NOT NULL AND financial_limit_currency IS NOT NULL "
            "AND delegation_evidence_reference IS NOT NULL AND delegation_policy_version_id IS NOT NULL)",
            name="ck_st_owner_assignment_financial_delegation_complete",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_by_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["ended_by_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["delegation_policy_version_id"],
            ["st_policy_version.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_st_owner_assignment_owner_uuid", "st_owner_assignment", ["owner_uuid"], unique=True)
    op.create_index("ix_st_owner_assignment_org_id", "st_owner_assignment", ["org_id"])
    op.create_index(
        "ix_st_owner_assignment_lookup",
        "st_owner_assignment",
        ["org_id", "user_id", "responsibility", "status"],
    )

    op.create_table(
        "st_policy_approval",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("approval_uuid", sa.String(length=100), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("policy_version_id", sa.Integer(), nullable=False),
        sa.Column("owner_assignment_id", sa.Integer(), nullable=False),
        sa.Column("responsibility", sa.String(length=32), nullable=False),
        sa.Column("approver_user_id", sa.Integer(), nullable=False),
        sa.Column("evidence_reference", sa.String(length=500), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"responsibility IN ({OWNER_FUNCTIONS})", name="ck_st_policy_approval_responsibility"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["policy_version_id"], ["st_policy_version.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_assignment_id"], ["st_owner_assignment.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approver_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "policy_version_id", "responsibility",
            name="uq_st_policy_approval_responsibility",
        ),
    )
    op.create_index("ix_st_policy_approval_approval_uuid", "st_policy_approval", ["approval_uuid"], unique=True)
    op.create_index("ix_st_policy_approval_org_id", "st_policy_approval", ["org_id"])
    op.create_index(
        "ix_st_policy_approval_org_policy",
        "st_policy_approval",
        ["org_id", "policy_version_id"],
    )

    op.create_table(
        "st_staff_scope",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scope_record_uuid", sa.String(length=100), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("capability", sa.String(length=64), nullable=False),
        sa.Column("scope_kind", sa.String(length=32), nullable=False),
        sa.Column("scope_uuid", sa.String(length=100), nullable=True),
        sa.Column("is_exclusion", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("evidence_reference", sa.String(length=500), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("revoked_by_user_id", sa.Integer(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_evidence_reference", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope_kind IN ('course', 'learner', 'case', 'offer', 'order', 'collection', 'function')",
            name="ck_st_staff_scope_kind",
        ),
        sa.CheckConstraint(
            "scope_uuid IS NOT NULL",
            name="ck_st_staff_scope_target_shape",
        ),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_st_staff_scope_status"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assignment_id"], ["st_owner_assignment.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["revoked_by_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_st_staff_scope_record_uuid", "st_staff_scope", ["scope_record_uuid"], unique=True)
    op.create_index("ix_st_staff_scope_org_capability", "st_staff_scope", ["org_id", "capability"])
    op.create_index("ix_st_staff_scope_assignment_status", "st_staff_scope", ["assignment_id", "status"])

    op.create_table(
        "st_business_event",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_uuid", sa.String(length=100), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_machine_id", sa.String(length=64), nullable=True),
        sa.Column("actor_capacity", sa.String(length=64), nullable=True),
        sa.Column("subject_uuid", sa.String(length=100), nullable=True),
        sa.Column("resource_kind", sa.String(length=32), nullable=True),
        sa.Column("resource_uuid", sa.String(length=100), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("before_state", sa.JSON(), nullable=True),
        sa.Column("after_state", sa.JSON(), nullable=True),
        sa.Column("reason", sa.String(length=2000), nullable=True),
        sa.Column("policy_refs", sa.JSON(), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("operation_uuid", sa.String(length=64), nullable=True),
        sa.Column("dedupe_key", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_st_business_event_dedupe"),
    )
    op.create_index("ix_st_business_event_event_uuid", "st_business_event", ["event_uuid"], unique=True)
    op.create_index("ix_st_business_event_org_resource", "st_business_event", ["org_id", "resource_uuid", "id"])
    op.create_index("ix_st_business_event_org_subject", "st_business_event", ["org_id", "subject_uuid", "id"])
    op.create_index("ix_st_business_event_org_created", "st_business_event", ["org_id", "created_at"])
    op.create_index("ix_st_business_event_correlation", "st_business_event", ["correlation_id"])

    op.create_table(
        "st_idempotency_record",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("record_uuid", sa.String(length=100), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=100), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="processing"),
        sa.Column("resource_kind", sa.String(length=32), nullable=True),
        sa.Column("resource_uuid", sa.String(length=100), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_projection", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('processing', 'completed')",
            name="ck_st_idempotency_status",
        ),
        sa.CheckConstraint(
            "(status = 'processing' AND completed_at IS NULL AND resource_uuid IS NULL) OR "
            "(status = 'completed' AND completed_at IS NOT NULL AND resource_uuid IS NOT NULL)",
            name="ck_st_idempotency_completion_shape",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id", "actor_user_id", "operation", "key_hash",
            name="uq_st_idempotency_actor_operation_key",
        ),
    )
    op.create_index("ix_st_idempotency_record_uuid", "st_idempotency_record", ["record_uuid"], unique=True)
    op.create_index("ix_st_idempotency_org_created", "st_idempotency_record", ["org_id", "created_at"])
    op.create_index("ix_st_idempotency_status_expiry", "st_idempotency_record", ["status", "expires_at"])
    op.create_index(
        "ix_st_idempotency_resource",
        "st_idempotency_record",
        ["org_id", "resource_kind", "resource_uuid"],
    )

    op.create_table(
        "st_outbox_record",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("record_uuid", sa.String(length=100), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("recipient_user_id", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("template_key", sa.String(length=80), nullable=False),
        sa.Column("resource_kind", sa.String(length=32), nullable=False),
        sa.Column("resource_uuid", sa.String(length=100), nullable=False),
        sa.Column("resource_version", sa.Integer(), nullable=True),
        sa.Column("authority_reference_uuid", sa.String(length=100), nullable=True),
        sa.Column("policy_refs", sa.JSON(), nullable=False),
        sa.Column("dedupe_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("provider_idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=100), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_message_reference", sa.String(length=160), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('required_notice', 'receipt', 'progress_report', 'optional_reminder', 'marketing')",
            name="ck_st_outbox_purpose",
        ),
        sa.CheckConstraint(
            "template_key IN ('required_notice_v1', 'receipt_v1', 'progress_report_v1', "
            "'optional_reminder_v1', 'marketing_v1')",
            name="ck_st_outbox_template",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'leased', 'retry', 'sent', 'suppressed', 'reconciliation_required', 'dead_letter')",
            name="ck_st_outbox_status",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_st_outbox_attempt_count_nonnegative"),
        sa.CheckConstraint(
            "resource_version IS NULL OR resource_version >= 1",
            name="ck_st_outbox_resource_version_positive",
        ),
        sa.CheckConstraint(
            "(status = 'leased' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'leased' AND lease_owner IS NULL AND lease_expires_at IS NULL)",
            name="ck_st_outbox_lease_shape",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "dedupe_hash", name="uq_st_outbox_org_dedupe"),
        sa.UniqueConstraint(
            "provider_idempotency_key",
            name="uq_st_outbox_provider_idempotency",
        ),
    )
    op.create_index("ix_st_outbox_due", "st_outbox_record", ["status", "next_attempt_at", "created_at"])
    op.create_index("ix_st_outbox_recipient", "st_outbox_record", ["org_id", "recipient_user_id", "created_at"])
    op.create_index("ix_st_outbox_resource", "st_outbox_record", ["org_id", "resource_kind", "resource_uuid"])
    op.create_index("ix_st_outbox_lease", "st_outbox_record", ["status", "lease_expires_at"])
    op.create_index("ix_st_outbox_record_uuid", "st_outbox_record", ["record_uuid"], unique=True)

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION reject_st_business_event_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'st_business_event rows are immutable';
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER st_business_event_immutable
            BEFORE UPDATE OR DELETE ON st_business_event
            FOR EACH ROW EXECUTE FUNCTION reject_st_business_event_mutation()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER st_business_event_immutable_update
            BEFORE UPDATE ON st_business_event
            BEGIN SELECT RAISE(ABORT, 'st_business_event rows are immutable'); END
            """
        )
        op.execute(
            """
            CREATE TRIGGER st_business_event_immutable_delete
            BEFORE DELETE ON st_business_event
            BEGIN SELECT RAISE(ABORT, 'st_business_event rows are immutable'); END
            """
        )


def downgrade() -> None:
    op.drop_index("ix_st_outbox_record_uuid", table_name="st_outbox_record")
    op.drop_index("ix_st_outbox_lease", table_name="st_outbox_record")
    op.drop_index("ix_st_outbox_resource", table_name="st_outbox_record")
    op.drop_index("ix_st_outbox_recipient", table_name="st_outbox_record")
    op.drop_index("ix_st_outbox_due", table_name="st_outbox_record")
    op.drop_table("st_outbox_record")

    op.drop_index("ix_st_idempotency_resource", table_name="st_idempotency_record")
    op.drop_index("ix_st_idempotency_status_expiry", table_name="st_idempotency_record")
    op.drop_index("ix_st_idempotency_org_created", table_name="st_idempotency_record")
    op.drop_index("ix_st_idempotency_record_uuid", table_name="st_idempotency_record")
    op.drop_table("st_idempotency_record")

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS st_business_event_immutable ON st_business_event")
        op.execute("DROP FUNCTION IF EXISTS reject_st_business_event_mutation()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS st_business_event_immutable_update")
        op.execute("DROP TRIGGER IF EXISTS st_business_event_immutable_delete")
    op.drop_index("ix_st_business_event_correlation", table_name="st_business_event")
    op.drop_index("ix_st_business_event_org_created", table_name="st_business_event")
    op.drop_index("ix_st_business_event_org_subject", table_name="st_business_event")
    op.drop_index("ix_st_business_event_org_resource", table_name="st_business_event")
    op.drop_index("ix_st_business_event_event_uuid", table_name="st_business_event")
    op.drop_table("st_business_event")

    op.drop_index("ix_st_staff_scope_org_capability", table_name="st_staff_scope")
    op.drop_index("ix_st_staff_scope_assignment_status", table_name="st_staff_scope")
    op.drop_index("ix_st_staff_scope_record_uuid", table_name="st_staff_scope")
    op.drop_table("st_staff_scope")

    op.drop_index("ix_st_policy_approval_org_policy", table_name="st_policy_approval")
    op.drop_index("ix_st_policy_approval_org_id", table_name="st_policy_approval")
    op.drop_index("ix_st_policy_approval_approval_uuid", table_name="st_policy_approval")
    op.drop_table("st_policy_approval")

    op.drop_index("ix_st_owner_assignment_lookup", table_name="st_owner_assignment")
    op.drop_index("ix_st_owner_assignment_org_id", table_name="st_owner_assignment")
    op.drop_index("ix_st_owner_assignment_owner_uuid", table_name="st_owner_assignment")
    op.drop_table("st_owner_assignment")

    op.drop_index("ix_st_policy_version_scope_status", table_name="st_policy_version")
    op.drop_index("ix_st_policy_version_org_id", table_name="st_policy_version")
    op.drop_index("ix_st_policy_version_policy_uuid", table_name="st_policy_version")
    op.drop_table("st_policy_version")
