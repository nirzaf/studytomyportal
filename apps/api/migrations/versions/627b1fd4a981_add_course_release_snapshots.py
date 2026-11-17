"""Add private course release snapshots.

Revision ID: 627b1fd4a981
Revises: 8a91ce74f623
"""

import sqlalchemy as sa
from alembic import op

revision = "627b1fd4a981"
down_revision = "8a91ce74f623"
branch_labels = None
depends_on = None


_COURSE_RELEASE_REFERENCE_CHECK = """
    NOT EXISTS (
        SELECT 1 FROM st_course_review AS review
        WHERE review.id = NEW.review_id AND review.org_id = NEW.org_id
          AND review.course_uuid = NEW.course_uuid AND review.decision = 'approved'
          AND review.revision_sha256 = NEW.snapshot_sha256
    )
    OR 4 <> (
        SELECT count(*) FROM st_policy_version AS policy
        JOIN organization AS org ON org.id = NEW.org_id
        WHERE policy.org_id = org.id AND policy.scope_kind = 'organization'
          AND policy.scope_uuid = org.org_uuid AND policy.status = 'approved'
          AND (
            (policy.id = NEW.assessment_policy_id AND policy.policy_key = 'D-08')
            OR (policy.id = NEW.progression_policy_id AND policy.policy_key = 'D-09')
            OR (policy.id = NEW.retention_policy_id AND policy.policy_key = 'D-13')
            OR (policy.id = NEW.rights_policy_id AND policy.policy_key = 'D-16')
          )
    )
"""


def upgrade() -> None:
    op.create_table(
        "st_course_release",
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
        sa.Column(
            "review_id",
            sa.BigInteger(),
            sa.ForeignKey("st_course_review.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("media_manifest", sa.JSON(), nullable=False),
        *(
            sa.Column(
                f"{scope}_policy_id",
                sa.Integer(),
                sa.ForeignKey("st_policy_version.id", ondelete="RESTRICT"),
                nullable=False,
            )
            for scope in ("assessment", "progression", "retention", "rights")
        ),
        sa.Column("retention_category", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("uuid", name="uq_st_course_release_uuid"),
        sa.UniqueConstraint("review_id", name="uq_st_course_release_review"),
        sa.CheckConstraint(
            "length(snapshot_sha256) = 64", name="ck_st_course_release_digest"
        ),
        sa.CheckConstraint(
            "length(retention_category) BETWEEN 1 AND 64",
            name="ck_st_course_release_retention",
        ),
    )
    op.create_index(
        "ix_st_course_release_course",
        "st_course_release",
        ["org_id", "course_uuid", "id"],
    )
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(f"""
            CREATE FUNCTION check_st_course_release_references() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF {_COURSE_RELEASE_REFERENCE_CHECK} THEN
                    RAISE EXCEPTION 'Study course release references are invalid';
                END IF;
                RETURN NEW;
            END;
            $$
        """)
        op.execute("""
            CREATE TRIGGER st_course_release_reference_guard
            BEFORE INSERT ON st_course_release
            FOR EACH ROW EXECUTE FUNCTION check_st_course_release_references()
        """)
        op.execute("""
            CREATE FUNCTION reject_st_course_release_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'Study course releases are immutable';
            END;
            $$
        """)
        op.execute("""
            CREATE TRIGGER st_course_release_immutable
            BEFORE UPDATE OR DELETE ON st_course_release
            FOR EACH ROW EXECUTE FUNCTION reject_st_course_release_mutation()
        """)
    elif dialect == "sqlite":
        op.execute(f"""
            CREATE TRIGGER st_course_release_reference_guard
            BEFORE INSERT ON st_course_release
            WHEN {_COURSE_RELEASE_REFERENCE_CHECK}
            BEGIN SELECT RAISE(ABORT, 'Study course release references are invalid'); END
        """)
        for action in ("UPDATE", "DELETE"):
            op.execute(f"""
                CREATE TRIGGER st_course_release_immutable_{action.lower()}
                BEFORE {action} ON st_course_release
                BEGIN SELECT RAISE(ABORT, 'Study course releases are immutable'); END
            """)


def downgrade() -> None:
    connection = op.get_bind()
    if (
        connection.scalar(sa.text("SELECT 1 FROM st_course_release LIMIT 1"))
        is not None
    ):
        raise RuntimeError("Retain stored course releases during rollback")
    op.drop_table("st_course_release")
    if connection.dialect.name == "postgresql":
        op.execute("DROP FUNCTION reject_st_course_release_mutation()")
        op.execute("DROP FUNCTION check_st_course_release_references()")
