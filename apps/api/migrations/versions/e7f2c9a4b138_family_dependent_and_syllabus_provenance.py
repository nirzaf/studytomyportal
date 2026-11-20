"""Add dependent guardian bindings and Sri Lankan syllabus/source provenance fields."""

import sqlalchemy as sa
from alembic import op


revision = "e7f2c9a4b138"
down_revision = "c4e8a1b7d209"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. st_evidence_file: guardian_authority_evidence bound to relationship_uuid
    op.add_column(
        "st_evidence_file",
        sa.Column("relationship_uuid", sa.String(length=100), nullable=True),
    )
    op.create_index(
        "ix_st_evidence_file_relationship",
        "st_evidence_file",
        ["org_id", "relationship_uuid"],
        unique=False,
    )
    op.drop_constraint("ck_st_evidence_file_purpose", "st_evidence_file", type_="check")
    op.create_check_constraint(
        "ck_st_evidence_file_purpose",
        "st_evidence_file",
        "purpose IN ('eligibility_evidence', 'guardian_authority_evidence', 'content_rights', 'content_accessibility', 'record_request_evidence', 'safeguarding_case_evidence')",
    )
    op.drop_constraint("ck_st_evidence_file_scope", "st_evidence_file", type_="check")
    op.create_check_constraint(
        "ck_st_evidence_file_scope",
        "st_evidence_file",
        "(purpose = 'eligibility_evidence' AND learner_profile_id IS NOT NULL "
        "AND relationship_uuid IS NULL AND course_uuid IS NULL AND revision_sha256 IS NULL AND record_request_event_uuid IS NULL AND safeguarding_case_uuid IS NULL) OR "
        "(purpose = 'guardian_authority_evidence' AND learner_profile_id IS NOT NULL "
        "AND relationship_uuid IS NOT NULL AND course_uuid IS NULL AND revision_sha256 IS NULL AND record_request_event_uuid IS NULL AND safeguarding_case_uuid IS NULL) OR "
        "(purpose IN ('content_rights', 'content_accessibility') AND learner_profile_id IS NULL "
        "AND relationship_uuid IS NULL AND course_uuid IS NOT NULL AND revision_sha256 IS NOT NULL AND length(revision_sha256) = 64 "
        "AND record_request_event_uuid IS NULL AND safeguarding_case_uuid IS NULL) OR "
        "(purpose = 'record_request_evidence' AND learner_profile_id IS NOT NULL "
        "AND relationship_uuid IS NULL AND course_uuid IS NULL AND revision_sha256 IS NULL AND record_request_event_uuid IS NOT NULL AND safeguarding_case_uuid IS NULL) OR "
        "(purpose = 'safeguarding_case_evidence' AND learner_profile_id IS NULL "
        "AND relationship_uuid IS NULL AND course_uuid IS NULL AND revision_sha256 IS NULL AND record_request_event_uuid IS NULL "
        "AND safeguarding_case_uuid IS NOT NULL)",
    )

    # 2. st_consent_event: guardian authority consent binding
    op.add_column(
        "st_consent_event",
        sa.Column(
            "relationship_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey("st_relationship.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column(
        "st_consent_event",
        sa.Column("relationship_authority_version", sa.Integer(), nullable=True),
    )
    op.drop_constraint("ck_st_consent_event_self_actor", "st_consent_event", type_="check")
    op.create_check_constraint(
        "ck_st_consent_event_self_actor",
        "st_consent_event",
        "(provenance = 'authenticated_self_service' AND actor_user_id = subject_user_id "
        "AND relationship_id IS NULL AND relationship_authority_version IS NULL) OR "
        "(provenance = 'authenticated_guardian_authority' AND actor_user_id <> subject_user_id "
        "AND relationship_id IS NOT NULL AND relationship_authority_version >= 1)",
    )
    op.drop_constraint("ck_st_consent_event_provenance", "st_consent_event", type_="check")
    op.create_check_constraint(
        "ck_st_consent_event_provenance",
        "st_consent_event",
        "provenance IN ('authenticated_self_service', 'authenticated_guardian_authority')",
    )

    # 3. st_report_recipient: explicit guardian recipient capacity
    op.alter_column(
        "st_report_recipient",
        "sharing_grant_id",
        existing_type=sa.BigInteger(),
        nullable=True,
    )
    op.add_column(
        "st_report_recipient",
        sa.Column(
            "recipient_capacity",
            sa.String(length=32),
            nullable=False,
            server_default="independent_sharing",
        ),
    )
    op.add_column(
        "st_report_recipient",
        sa.Column(
            "relationship_id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            sa.ForeignKey("st_relationship.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_st_report_recipient_capacity",
        "st_report_recipient",
        "(recipient_capacity = 'independent_sharing' AND sharing_grant_id IS NOT NULL AND relationship_id IS NULL) OR "
        "(recipient_capacity = 'guardian_authority' AND sharing_grant_id IS NULL AND relationship_id IS NOT NULL)",
    )

    # 4. st_curriculum_revision & st_draft_material: optional programme & source provenance
    op.add_column(
        "st_curriculum_revision",
        sa.Column("programme_metadata", sa.JSON(), nullable=True),
    )
    op.add_column(
        "st_draft_material",
        sa.Column("source_provenance", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    bind = op.get_bind()
    checks = (
        ("st_draft_material", "source_provenance IS NOT NULL"),
        ("st_curriculum_revision", "programme_metadata IS NOT NULL"),
        ("st_report_recipient", "sharing_grant_id IS NULL OR relationship_id IS NOT NULL"),
        ("st_consent_event", "relationship_id IS NOT NULL OR actor_user_id <> subject_user_id"),
        ("st_evidence_file", "relationship_uuid IS NOT NULL OR purpose = 'guardian_authority'"),
    )
    for table_name, predicate in checks:
        count = bind.execute(sa.text(f"SELECT COUNT(*) FROM {table_name} WHERE {predicate}")).scalar_one()
        if count:
            raise RuntimeError(
                f"Refusing lossy downgrade of revision e7f2c9a4b138: {count} populated row(s) in "
                f"{table_name} match ({predicate}). Restore from a verified backup or apply a forward repair."
            )

    op.drop_column("st_draft_material", "source_provenance")
    op.drop_column("st_curriculum_revision", "programme_metadata")

    op.drop_constraint("ck_st_report_recipient_capacity", "st_report_recipient", type_="check")
    op.drop_column("st_report_recipient", "relationship_id")
    op.drop_column("st_report_recipient", "recipient_capacity")
    op.alter_column(
        "st_report_recipient",
        "sharing_grant_id",
        existing_type=sa.BigInteger(),
        nullable=False,
    )

    op.drop_constraint("ck_st_consent_event_provenance", "st_consent_event", type_="check")
    op.create_check_constraint(
        "ck_st_consent_event_provenance",
        "st_consent_event",
        "provenance = 'authenticated_self_service'",
    )
    op.drop_constraint("ck_st_consent_event_self_actor", "st_consent_event", type_="check")
    op.create_check_constraint(
        "ck_st_consent_event_self_actor",
        "st_consent_event",
        "actor_user_id = subject_user_id",
    )
    op.drop_column("st_consent_event", "relationship_authority_version")
    op.drop_column("st_consent_event", "relationship_id")

    op.drop_constraint("ck_st_evidence_file_scope", "st_evidence_file", type_="check")
    op.create_check_constraint(
        "ck_st_evidence_file_scope",
        "st_evidence_file",
        "(purpose = 'eligibility_evidence' AND learner_profile_id IS NOT NULL "
        "AND course_uuid IS NULL AND revision_sha256 IS NULL AND record_request_event_uuid IS NULL AND safeguarding_case_uuid IS NULL) OR "
        "(purpose IN ('content_rights', 'content_accessibility') AND learner_profile_id IS NULL "
        "AND course_uuid IS NOT NULL AND revision_sha256 IS NOT NULL AND length(revision_sha256) = 64 "
        "AND record_request_event_uuid IS NULL AND safeguarding_case_uuid IS NULL) OR "
        "(purpose = 'record_request_evidence' AND learner_profile_id IS NOT NULL "
        "AND course_uuid IS NULL AND revision_sha256 IS NULL AND record_request_event_uuid IS NOT NULL AND safeguarding_case_uuid IS NULL) OR "
        "(purpose = 'safeguarding_case_evidence' AND learner_profile_id IS NULL "
        "AND course_uuid IS NULL AND revision_sha256 IS NULL AND record_request_event_uuid IS NULL "
        "AND safeguarding_case_uuid IS NOT NULL)",
    )
    op.drop_constraint("ck_st_evidence_file_purpose", "st_evidence_file", type_="check")
    op.create_check_constraint(
        "ck_st_evidence_file_purpose",
        "st_evidence_file",
        "purpose IN ('eligibility_evidence', 'content_rights', 'content_accessibility', 'record_request_evidence', 'safeguarding_case_evidence')",
    )
    op.drop_index("ix_st_evidence_file_relationship", table_name="st_evidence_file")
    op.drop_column("st_evidence_file", "relationship_uuid")
