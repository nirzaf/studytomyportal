"""Add durable adaptive attempt and evidence storage.

Revision ID: d6b2beaf0c43
Revises: 7bfc5ca4ad19
"""

import sqlalchemy as sa
from alembic import op

from src.core.adaptive_evidence_schema import ensure_adaptive_evidence_guards

revision = "d6b2beaf0c43"
down_revision = "7bfc5ca4ad19"
branch_labels = None
depends_on = None

_PARENT_KEYS = (
    ("st_learner_profile", "uq_st_learner_profile_org_id", ["org_id", "id"]),
    (
        "st_diagnostic_revision",
        "uq_st_diagnostic_attempt_binding",
        ["org_id", "curriculum_revision_id", "id", "blueprint_uuid"],
    ),
    (
        "st_item_revision",
        "uq_st_item_issue_binding",
        ["org_id", "curriculum_revision_id", "concept_id", "id", "item_family_uuid"],
    ),
)
_TABLES = (
    "st_concept_mastery",
    "st_learning_evidence",
    "st_attempt_item",
    "st_learning_attempt",
)


def upgrade() -> None:
    for table, name, columns in _PARENT_KEYS:
        with op.batch_alter_table(table) as batch:
            batch.create_unique_constraint(name, columns)
    op.create_table(
        "st_learning_attempt",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uuid", sa.String(length=64), nullable=False),
        sa.Column("learner_profile_id", sa.BigInteger(), nullable=False),
        sa.Column("curriculum_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("diagnostic_revision_id", sa.BigInteger(), nullable=True),
        sa.Column("blueprint_uuid", sa.String(length=64), nullable=True),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_capacity", sa.String(length=16), nullable=False),
        sa.Column("actor_reference", sa.String(length=100), nullable=True),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("next_ordinal", sa.Integer(), nullable=False),
        sa.Column("source_course_uuid", sa.String(length=255), nullable=False),
        sa.Column("source_activity_uuid", sa.String(length=89), nullable=True),
        sa.Column("source_round_uuid", sa.String(length=64), nullable=True),
        sa.Column("source_assignment_uuid", sa.String(length=100), nullable=True),
        sa.Column("source_submission_uuid", sa.String(length=100), nullable=True),
        sa.Column("source_attempt_number", sa.Integer(), nullable=True),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("policy_version_ids", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(actor_capacity IN ('learner', 'reviewer') AND actor_user_id IS NOT NULL) OR (actor_capacity IN ('delegated', 'system') AND actor_reference IS NOT NULL)",
            name="ck_st_attempt_actor",
        ),
        sa.CheckConstraint(
            "(kind = 'assignment' AND source_round_uuid IS NOT NULL AND source_assignment_uuid IS NOT NULL AND source_submission_uuid IS NOT NULL AND source_attempt_number IS NOT NULL AND source_attempt_number >= 1) OR (kind <> 'assignment' AND source_round_uuid IS NULL AND source_assignment_uuid IS NULL AND source_submission_uuid IS NULL AND source_attempt_number IS NULL)",
            name="ck_st_attempt_round",
        ),
        sa.CheckConstraint(
            "(kind = 'diagnostic' AND diagnostic_revision_id IS NOT NULL AND blueprint_uuid IS NOT NULL) OR (kind IN ('practice', 'assignment') AND diagnostic_revision_id IS NULL AND blueprint_uuid IS NULL)",
            name="ck_st_attempt_kind",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND ended_at IS NULL) OR (status IN ('completed', 'abandoned', 'expired', 'invalidated') AND ended_at IS NOT NULL AND ended_at >= started_at)",
            name="ck_st_attempt_status",
        ),
        sa.CheckConstraint(
            "expires_at > started_at AND version >= 1 AND next_ordinal >= 1 AND length(manifest_digest) = 64",
            name="ck_st_attempt_version",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            [
                "org_id",
                "curriculum_revision_id",
                "diagnostic_revision_id",
                "blueprint_uuid",
            ],
            [
                "st_diagnostic_revision.org_id",
                "st_diagnostic_revision.curriculum_revision_id",
                "st_diagnostic_revision.id",
                "st_diagnostic_revision.blueprint_uuid",
            ],
            name="fk_st_attempt_diagnostic",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "curriculum_revision_id"],
            ["st_curriculum_revision.org_id", "st_curriculum_revision.id"],
            name="fk_st_attempt_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "learner_profile_id"],
            ["st_learner_profile.org_id", "st_learner_profile.id"],
            name="fk_st_attempt_learner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "id",
            "learner_profile_id",
            "curriculum_revision_id",
            name="uq_st_attempt_item_binding",
        ),
        sa.UniqueConstraint(
            "org_id", "source_round_uuid", name="uq_st_attempt_source_round"
        ),
        sa.UniqueConstraint(
            "org_id",
            "source_submission_uuid",
            "source_attempt_number",
            name="uq_st_attempt_native_round",
        ),
        sa.UniqueConstraint("uuid", name="uq_st_attempt_uuid"),
    )
    op.create_index(
        "uq_st_attempt_active_diagnostic",
        "st_learning_attempt",
        ["org_id", "learner_profile_id", "blueprint_uuid"],
        unique=True,
        postgresql_where=sa.text("kind = 'diagnostic' AND status = 'active'"),
        sqlite_where=sa.text("kind = 'diagnostic' AND status = 'active'"),
    )
    op.create_table(
        "st_attempt_item",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uuid", sa.String(length=64), nullable=False),
        sa.Column("attempt_id", sa.BigInteger(), nullable=False),
        sa.Column("learner_profile_id", sa.BigInteger(), nullable=False),
        sa.Column("curriculum_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("concept_id", sa.BigInteger(), nullable=False),
        sa.Column("item_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("item_family_uuid", sa.String(length=64), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("is_diagnostic", sa.Boolean(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_locator", sa.String(length=100), nullable=True),
        sa.Column("response_state", sa.String(length=12), nullable=False),
        sa.Column("response_version", sa.Integer(), nullable=False),
        sa.Column("normalized_response", sa.String(length=128), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_digest", sa.String(length=64), nullable=True),
        sa.Column("help_count", sa.Integer(), nullable=False),
        sa.Column("exposure_count", sa.Integer(), nullable=False),
        sa.Column("skip_reason", sa.String(length=40), nullable=True),
        sa.CheckConstraint(
            "(response_state = 'issued' AND normalized_response IS NULL AND response_digest IS NULL AND responded_at IS NULL AND skip_reason IS NULL) OR (response_state = 'answered' AND normalized_response IS NOT NULL AND response_digest IS NOT NULL AND length(response_digest) = 64 AND responded_at IS NOT NULL AND responded_at >= issued_at AND skip_reason IS NULL) OR (response_state = 'skipped' AND normalized_response IS NULL AND response_digest IS NULL AND responded_at IS NOT NULL AND responded_at >= issued_at AND skip_reason IS NOT NULL)",
            name="ck_st_attempt_item_response",
        ),
        sa.CheckConstraint(
            "NOT is_diagnostic OR help_count = 0",
            name="ck_st_attempt_item_diagnostic_help",
        ),
        sa.CheckConstraint(
            "ordinal >= 1 AND response_version >= 1 AND help_count >= 0 AND exposure_count >= 0",
            name="ck_st_attempt_item_version",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "attempt_id", "learner_profile_id", "curriculum_revision_id"],
            [
                "st_learning_attempt.org_id",
                "st_learning_attempt.id",
                "st_learning_attempt.learner_profile_id",
                "st_learning_attempt.curriculum_revision_id",
            ],
            name="fk_st_attempt_item_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "org_id",
                "curriculum_revision_id",
                "concept_id",
                "item_revision_id",
                "item_family_uuid",
            ],
            [
                "st_item_revision.org_id",
                "st_item_revision.curriculum_revision_id",
                "st_item_revision.concept_id",
                "st_item_revision.id",
                "st_item_revision.item_family_uuid",
            ],
            name="fk_st_attempt_item_definition",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attempt_id", "ordinal", name="uq_st_attempt_item_ordinal"),
        sa.UniqueConstraint(
            "org_id",
            "id",
            "learner_profile_id",
            "curriculum_revision_id",
            "concept_id",
            "uuid",
            name="uq_st_attempt_item_evidence_binding",
        ),
        sa.UniqueConstraint("uuid", name="uq_st_attempt_item_uuid"),
    )
    op.create_index(
        "uq_st_attempt_diagnostic_item_family",
        "st_attempt_item",
        ["attempt_id", "item_family_uuid"],
        unique=True,
        postgresql_where=sa.text("is_diagnostic = true"),
        sqlite_where=sa.text("is_diagnostic = true"),
    )
    op.create_index(
        "uq_st_attempt_item_outstanding",
        "st_attempt_item",
        ["attempt_id"],
        unique=True,
        postgresql_where=sa.text("is_diagnostic = true AND response_state = 'issued'"),
        sqlite_where=sa.text("is_diagnostic = true AND response_state = 'issued'"),
    )
    op.create_table(
        "st_learning_evidence",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uuid", sa.String(length=64), nullable=False),
        sa.Column("learner_profile_id", sa.BigInteger(), nullable=False),
        sa.Column("curriculum_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("concept_id", sa.BigInteger(), nullable=False),
        sa.Column("attempt_item_id", sa.BigInteger(), nullable=False),
        sa.Column("logical_source_uuid", sa.String(length=64), nullable=False),
        sa.Column(
            "evidence_sequence",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.Column("evaluator_version", sa.String(length=40), nullable=False),
        sa.Column("policy_version_ids", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.String(length=12), nullable=False),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("independence", sa.String(length=12), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("eligibility_reason", sa.String(length=64), nullable=False),
        sa.Column("misconception_code", sa.String(length=64), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("actor_reference", sa.String(length=100), nullable=True),
        sa.Column("supersedes_id", sa.BigInteger(), nullable=True),
        sa.Column("correction_reason", sa.String(length=64), nullable=True),
        sa.Column("is_retraction", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "(outcome = 'correct' AND score = 1 AND score IS NOT NULL) OR (outcome = 'incorrect' AND score = 0 AND score IS NOT NULL) OR (outcome = 'not_scored' AND score IS NULL)",
            name="ck_st_evidence_score",
        ),
        sa.CheckConstraint(
            "independence IN ('independent', 'assisted', 'unknown') AND (NOT eligible OR (independence = 'independent' AND outcome IN ('correct', 'incorrect') AND NOT is_retraction)) AND (NOT is_retraction OR outcome = 'not_scored')",
            name="ck_st_evidence_eligibility",
        ),
        sa.CheckConstraint(
            "(actor_user_id IS NULL) <> (actor_reference IS NULL)",
            name="ck_st_evidence_actor",
        ),
        sa.CheckConstraint(
            "(result_revision = 1 AND supersedes_id IS NULL AND correction_reason IS NULL AND NOT is_retraction) OR (result_revision > 1 AND supersedes_id IS NOT NULL AND correction_reason IS NOT NULL AND supersedes_id <> id)",
            name="ck_st_evidence_correction",
        ),
        sa.CheckConstraint(
            "evidence_sequence >= 1 AND result_revision >= 1",
            name="ck_st_evidence_revision",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            [
                "org_id",
                "attempt_item_id",
                "learner_profile_id",
                "curriculum_revision_id",
                "concept_id",
                "logical_source_uuid",
            ],
            [
                "st_attempt_item.org_id",
                "st_attempt_item.id",
                "st_attempt_item.learner_profile_id",
                "st_attempt_item.curriculum_revision_id",
                "st_attempt_item.concept_id",
                "st_attempt_item.uuid",
            ],
            name="fk_st_evidence_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "learner_profile_id", "logical_source_uuid", "supersedes_id"],
            [
                "st_learning_evidence.org_id",
                "st_learning_evidence.learner_profile_id",
                "st_learning_evidence.logical_source_uuid",
                "st_learning_evidence.id",
            ],
            name="fk_st_evidence_predecessor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "logical_source_uuid", "result_revision", name="uq_st_evidence_revision"
        ),
        sa.UniqueConstraint(
            "org_id",
            "learner_profile_id",
            "evidence_sequence",
            name="uq_st_evidence_sequence",
        ),
        sa.UniqueConstraint(
            "org_id",
            "learner_profile_id",
            "logical_source_uuid",
            "id",
            name="uq_st_evidence_binding",
        ),
        sa.UniqueConstraint("supersedes_id", name="uq_st_evidence_successor"),
        sa.UniqueConstraint("uuid", name="uq_st_evidence_uuid"),
    )
    op.create_index(
        "ix_st_evidence_concept_sequence",
        "st_learning_evidence",
        ["org_id", "learner_profile_id", "concept_id", "evidence_sequence"],
        unique=False,
    )
    op.create_table(
        "st_concept_mastery",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False
        ),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("uuid", sa.String(length=64), nullable=False),
        sa.Column("learner_profile_id", sa.BigInteger(), nullable=False),
        sa.Column("curriculum_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("concept_id", sa.BigInteger(), nullable=False),
        sa.Column("mastery_policy_uuid", sa.String(length=100), nullable=False),
        sa.Column("mastery_policy_digest", sa.String(length=64), nullable=False),
        sa.Column("reducer_version", sa.String(length=40), nullable=False),
        sa.Column("evidence_watermark", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("eligible_count", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("assisted_count", sa.Integer(), nullable=False),
        sa.Column("unknown_count", sa.Integer(), nullable=False),
        sa.Column("score", sa.Numeric(precision=10, scale=9), nullable=True),
        sa.Column("latest_independent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_due", sa.Boolean(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(eligible_count = 0 AND state = 'not_assessed' AND score IS NULL AND latest_independent_at IS NULL) OR (eligible_count > 0 AND state IN ('insufficient_evidence', 'developing', 'mastered') AND score IS NOT NULL AND score BETWEEN 0 AND 1 AND latest_independent_at IS NOT NULL)",
            name="ck_st_mastery_score",
        ),
        sa.CheckConstraint(
            "evidence_watermark >= 0 AND version >= 1 AND success_count BETWEEN 0 AND eligible_count AND assisted_count >= 0 AND unknown_count >= 0 AND length(mastery_policy_digest) = 64",
            name="ck_st_mastery_counts",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "curriculum_revision_id", "concept_id"],
            ["st_concept.org_id", "st_concept.curriculum_revision_id", "st_concept.id"],
            name="fk_st_mastery_concept",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "learner_profile_id"],
            ["st_learner_profile.org_id", "st_learner_profile.id"],
            name="fk_st_mastery_learner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "org_id",
            "learner_profile_id",
            "concept_id",
            "mastery_policy_digest",
            name="uq_st_mastery_projection",
        ),
        sa.UniqueConstraint("uuid", name="uq_st_mastery_uuid"),
    )

    ensure_adaptive_evidence_guards(op.get_bind())


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("LOCK TABLE " + ", ".join(_TABLES) + " IN ACCESS EXCLUSIVE MODE")
        )
    for table in _TABLES:
        if connection.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError(
                "Retained adaptive rounds or evidence exist. Roll back application code and retain the schema."
            )
    for table in _TABLES:
        op.drop_table(table)
        if connection.dialect.name == "postgresql":
            op.execute(f"DROP FUNCTION IF EXISTS guard_{table}()")
    for table, name, _columns in reversed(_PARENT_KEYS):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(name, type_="unique")
