"""Add private, immutable Studytomy offer draft versions.

Revision ID: d4a7c9e2b1f0
Revises: c91c2b73468f
Create Date: 2026-11-16

Only draft records exist in this migration. Customer acceptance, publication,
checkout, price approval, and entitlement activation remain in later tasks.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4a7c9e2b1f0"
down_revision: Union[str, None] = "c91c2b73468f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "st_offer",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("offer_uuid", sa.String(length=100), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("offer_kind", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("current_version_no", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status = 'draft'", name="ck_st_offer_draft_only"),
        sa.CheckConstraint(
            "offer_kind IN ('course', 'programme', 'membership')",
            name="ck_st_offer_kind",
        ),
        sa.CheckConstraint(
            "current_version_no >= 1", name="ck_st_offer_version_positive"
        ),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["user.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_st_offer_uuid", "st_offer", ["offer_uuid"], unique=True)
    op.create_index("ix_st_offer_org_status", "st_offer", ["org_id", "status"])

    op.create_table(
        "st_offer_version",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("version_uuid", sa.String(length=100), nullable=False),
        sa.Column("offer_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("terms", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version_no >= 1", name="ck_st_offer_version_number_positive"),
        sa.CheckConstraint(
            "length(content_sha256) = 64", name="ck_st_offer_version_sha256_length"
        ),
        sa.ForeignKeyConstraint(["offer_id"], ["st_offer.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["user.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("offer_id", "version_no", name="uq_st_offer_version_sequence"),
    )
    op.create_index(
        "ix_st_offer_version_uuid", "st_offer_version", ["version_uuid"], unique=True
    )
    op.create_index(
        "ix_st_offer_version_offer_created",
        "st_offer_version",
        ["offer_id", "version_no"],
    )

    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION protect_st_offer_draft_root() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'st_offer draft roots are retained';
                END IF;
                IF NEW.offer_uuid IS DISTINCT FROM OLD.offer_uuid
                   OR NEW.org_id IS DISTINCT FROM OLD.org_id
                   OR NEW.offer_kind IS DISTINCT FROM OLD.offer_kind
                   OR NEW.status IS DISTINCT FROM 'draft'
                   OR NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR NEW.current_version_no < OLD.current_version_no
                   OR NEW.current_version_no > OLD.current_version_no + 1 THEN
                    RAISE EXCEPTION 'st_offer roots are draft-only and version-monotonic';
                END IF;
                IF NEW.current_version_no = OLD.current_version_no + 1
                   AND NOT EXISTS (
                       SELECT 1 FROM st_offer_version
                       WHERE offer_id = OLD.id AND version_no = NEW.current_version_no
                   ) THEN
                    RAISE EXCEPTION 'the next st_offer_version must exist before advancing the draft';
                END IF;
                RETURN NEW;
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER st_offer_draft_root_protected
            BEFORE UPDATE OR DELETE ON st_offer
            FOR EACH ROW EXECUTE FUNCTION protect_st_offer_draft_root()
            """
        )
        op.execute(
            """
            CREATE FUNCTION reject_st_offer_version_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'st_offer_version rows are immutable';
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER st_offer_version_immutable
            BEFORE UPDATE OR DELETE ON st_offer_version
            FOR EACH ROW EXECUTE FUNCTION reject_st_offer_version_mutation()
            """
        )
    elif dialect == "sqlite":
        op.execute(
            """
            CREATE TRIGGER st_offer_draft_root_protected_update
            BEFORE UPDATE ON st_offer
            WHEN NEW.offer_uuid IS NOT OLD.offer_uuid
              OR NEW.org_id IS NOT OLD.org_id
              OR NEW.offer_kind IS NOT OLD.offer_kind
              OR NEW.status <> 'draft'
              OR NEW.created_by_user_id IS NOT OLD.created_by_user_id
              OR NEW.created_at IS NOT OLD.created_at
              OR NEW.current_version_no < OLD.current_version_no
              OR NEW.current_version_no > OLD.current_version_no + 1
              OR (
                  NEW.current_version_no = OLD.current_version_no + 1
                  AND NOT EXISTS (
                      SELECT 1 FROM st_offer_version
                      WHERE offer_id = OLD.id AND version_no = NEW.current_version_no
                  )
              )
            BEGIN SELECT RAISE(ABORT, 'st_offer roots are draft-only and version-monotonic'); END
            """
        )
        op.execute(
            """
            CREATE TRIGGER st_offer_draft_root_protected_delete
            BEFORE DELETE ON st_offer
            BEGIN SELECT RAISE(ABORT, 'st_offer draft roots are retained'); END
            """
        )
        op.execute(
            """
            CREATE TRIGGER st_offer_version_immutable_update
            BEFORE UPDATE ON st_offer_version
            BEGIN SELECT RAISE(ABORT, 'st_offer_version rows are immutable'); END
            """
        )
        op.execute(
            """
            CREATE TRIGGER st_offer_version_immutable_delete
            BEFORE DELETE ON st_offer_version
            BEGIN SELECT RAISE(ABORT, 'st_offer_version rows are immutable'); END
            """
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS st_offer_version_immutable ON st_offer_version")
        op.execute("DROP FUNCTION IF EXISTS reject_st_offer_version_mutation()")
        op.execute("DROP TRIGGER IF EXISTS st_offer_draft_root_protected ON st_offer")
        op.execute("DROP FUNCTION IF EXISTS protect_st_offer_draft_root()")
    elif dialect == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS st_offer_draft_root_protected_update")
        op.execute("DROP TRIGGER IF EXISTS st_offer_draft_root_protected_delete")
        op.execute("DROP TRIGGER IF EXISTS st_offer_version_immutable_update")
        op.execute("DROP TRIGGER IF EXISTS st_offer_version_immutable_delete")

    op.drop_index("ix_st_offer_version_offer_created", table_name="st_offer_version")
    op.drop_index("ix_st_offer_version_uuid", table_name="st_offer_version")
    op.drop_table("st_offer_version")
    op.drop_index("ix_st_offer_org_status", table_name="st_offer")
    op.drop_index("ix_st_offer_uuid", table_name="st_offer")
    op.drop_table("st_offer")
