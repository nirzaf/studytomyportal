"""Add the retained course review decision record.

Revision ID: 8a91ce74f623
Revises: dc48e62a7b91
"""

import sqlalchemy as sa
from alembic import op

revision = "8a91ce74f623"
down_revision = "dc48e62a7b91"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "st_course_review",
        sa.Column(
            "id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True
        ),
        sa.Column("uuid", sa.String(64), nullable=False),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organization.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("course_uuid", sa.String(255), nullable=False),
        sa.Column("revision_sha256", sa.String(64), nullable=False),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "reviewer_user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "reviewer_assignment_id",
            sa.Integer(),
            sa.ForeignKey("st_owner_assignment.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "rights_evidence_id",
            sa.BigInteger(),
            sa.ForeignKey("st_evidence_file.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "accessibility_evidence_id",
            sa.BigInteger(),
            sa.ForeignKey("st_evidence_file.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("policy_version_ids", sa.JSON(), nullable=False),
        sa.Column("decision", sa.String(24), nullable=False),
        sa.Column("accessibility_confirmed", sa.Boolean(), nullable=False),
        sa.Column("assessment_confirmed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("uuid", name="uq_st_course_review_uuid"),
        sa.CheckConstraint(
            "length(revision_sha256) = 64", name="ck_st_course_review_digest"
        ),
        sa.CheckConstraint(
            "owner_user_id <> reviewer_user_id", name="ck_st_course_review_independent"
        ),
        sa.CheckConstraint(
            "rights_evidence_id <> accessibility_evidence_id",
            name="ck_st_course_review_evidence",
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'changes_requested') AND "
            "(decision <> 'approved' OR (accessibility_confirmed AND assessment_confirmed))",
            name="ck_st_course_review_findings",
        ),
    )
    op.create_index(
        "ix_st_course_review_candidate",
        "st_course_review",
        ["org_id", "course_uuid", "revision_sha256", "id"],
    )
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("""
            CREATE FUNCTION reject_st_course_review_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'Study course reviews are immutable';
            END;
            $$
        """)
        op.execute("""
            CREATE TRIGGER st_course_review_immutable
            BEFORE UPDATE OR DELETE ON st_course_review
            FOR EACH ROW EXECUTE FUNCTION reject_st_course_review_mutation()
        """)
    elif dialect == "sqlite":
        for action in ("UPDATE", "DELETE"):
            op.execute(f"""
                CREATE TRIGGER st_course_review_immutable_{action.lower()}
                BEFORE {action} ON st_course_review
                BEGIN SELECT RAISE(ABORT, 'Study course reviews are immutable'); END
            """)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT 1 FROM st_course_review LIMIT 1")) is not None:
        raise RuntimeError("Retain stored course review decisions during rollback")
    op.drop_table("st_course_review")
    if connection.dialect.name == "postgresql":
        op.execute("DROP FUNCTION reject_st_course_review_mutation()")
