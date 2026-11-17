"""Create purpose-scoped service case records with tenant-bound exclusions."""

import sqlalchemy as sa
from alembic import op

revision = "72c1a8f0d4e6"
down_revision = "61e73f9b4c20"
branch_labels = None
depends_on = None


_ORIGINAL_PURPOSES = "'eligibility_evidence', 'content_rights', 'content_accessibility', 'record_request_evidence'"
_ORIGINAL_SCOPES = (
    "(purpose = 'eligibility_evidence' AND learner_profile_id IS NOT NULL "
    "AND course_uuid IS NULL AND revision_sha256 IS NULL AND record_request_event_uuid IS NULL) OR "
    "(purpose IN ('content_rights', 'content_accessibility') AND learner_profile_id IS NULL "
    "AND course_uuid IS NOT NULL AND revision_sha256 IS NOT NULL AND length(revision_sha256) = 64 "
    "AND record_request_event_uuid IS NULL) OR "
    "(purpose = 'record_request_evidence' AND learner_profile_id IS NOT NULL "
    "AND course_uuid IS NULL AND revision_sha256 IS NULL AND record_request_event_uuid IS NOT NULL)"
)


def upgrade() -> None:
    op.create_table(
        "st_service_case",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("case_uuid", sa.String(36), nullable=False),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reporter_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT")),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("org_id", "case_uuid", name="uq_st_service_case_org_uuid"),
        sa.CheckConstraint(
            "category IN ('support', 'safeguarding')",
            name="ck_st_service_case_category",
        ),
        sa.CheckConstraint(
            "(status = 'acknowledged' AND owner_user_id IS NULL) OR "
            "(status IN ('assigned', 'awaiting_information', 'under_review', 'resolved', 'closed') "
            "AND owner_user_id IS NOT NULL)",
            name="ck_st_service_case_state_owner",
        ),
        sa.CheckConstraint(
            "owner_user_id IS NULL OR owner_user_id <> reporter_user_id",
            name="ck_st_service_case_owner_not_reporter",
        ),
        sa.CheckConstraint("version >= 1", name="ck_st_service_case_version"),
    )
    op.create_index("ix_st_service_case_org_state", "st_service_case", ["org_id", "category", "status", "id"])
    op.create_index("ix_st_service_case_reporter", "st_service_case", ["org_id", "reporter_user_id", "id"])
    op.create_table(
        "st_service_case_exclusion",
        sa.Column("org_id", sa.Integer(), primary_key=True),
        sa.Column("case_uuid", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), primary_key=True),
        sa.ForeignKeyConstraint(
            ["org_id", "case_uuid"],
            ["st_service_case.org_id", "st_service_case.case_uuid"],
            name="fk_st_service_case_exclusion_org_case",
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "st_safeguarding_narrative",
        sa.Column("org_id", sa.Integer(), primary_key=True),
        sa.Column("case_uuid", sa.String(36), primary_key=True),
        sa.Column("narrative", sa.Text(), nullable=False),
        sa.Column(
            "retention_policy_id", sa.Integer(), sa.ForeignKey("st_policy_version.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("retention_category", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "case_uuid"], ["st_service_case.org_id", "st_service_case.case_uuid"],
            name="fk_st_safeguarding_narrative_org_case", ondelete="RESTRICT",
        ),
        sa.CheckConstraint("length(narrative) BETWEEN 1 AND 8000", name="ck_st_safeguarding_narrative_length"),
    )
    with op.batch_alter_table("st_evidence_file") as evidence:
        evidence.add_column(sa.Column("safeguarding_case_uuid", sa.String(36)))
        evidence.drop_constraint("ck_st_evidence_file_purpose", type_="check")
        evidence.drop_constraint("ck_st_evidence_file_scope", type_="check")
        evidence.create_check_constraint(
            "ck_st_evidence_file_purpose",
            f"purpose IN ({_ORIGINAL_PURPOSES}, 'safeguarding_case_evidence')",
        )
        evidence.create_check_constraint(
            "ck_st_evidence_file_scope",
            f"(({_ORIGINAL_SCOPES}) AND safeguarding_case_uuid IS NULL) OR "
            "(purpose = 'safeguarding_case_evidence' AND learner_profile_id IS NULL "
            "AND course_uuid IS NULL AND revision_sha256 IS NULL AND record_request_event_uuid IS NULL "
            "AND safeguarding_case_uuid IS NOT NULL)",
        )
        evidence.create_foreign_key(
            "fk_st_evidence_file_safeguarding_case", "st_service_case",
            ["org_id", "safeguarding_case_uuid"], ["org_id", "case_uuid"], ondelete="RESTRICT",
        )
        evidence.create_index(
            "ix_st_evidence_file_safeguarding_case", ["org_id", "safeguarding_case_uuid"],
        )


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE st_service_case, st_service_case_exclusion IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(sa.text("SELECT 1 FROM st_service_case LIMIT 1")).first():
        raise RuntimeError("Retained service cases must be resolved before downgrading this schema.")
    with op.batch_alter_table("st_evidence_file") as evidence:
        evidence.drop_index("ix_st_evidence_file_safeguarding_case")
        evidence.drop_constraint("fk_st_evidence_file_safeguarding_case", type_="foreignkey")
        evidence.drop_constraint("ck_st_evidence_file_purpose", type_="check")
        evidence.drop_constraint("ck_st_evidence_file_scope", type_="check")
        evidence.create_check_constraint("ck_st_evidence_file_purpose", f"purpose IN ({_ORIGINAL_PURPOSES})")
        evidence.create_check_constraint("ck_st_evidence_file_scope", _ORIGINAL_SCOPES)
        evidence.drop_column("safeguarding_case_uuid")
    op.drop_table("st_safeguarding_narrative")
    op.drop_table("st_service_case_exclusion")
    op.drop_table("st_service_case")
