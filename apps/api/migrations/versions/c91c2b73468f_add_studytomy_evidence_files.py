"""Add private learner evidence-file metadata sidecars.

Revision ID: c91c2b73468f
Revises: b72a11c83e2f
Create Date: 2026-11-15

File bytes stay in the configured storage adapter. The database retains the
owner, purpose, checksum, approved policy references and upload state.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c91c2b73468f"
down_revision: Union[str, None] = "b72a11c83e2f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "st_evidence_file",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("evidence_uuid", sa.String(length=100), nullable=False),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("learner_profile_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), nullable=False),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("storage_reference", sa.String(length=700), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("policy_version_ids", sa.JSON(), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("retention_category", sa.String(length=64), nullable=False),
        sa.Column("upload_status", sa.String(length=32), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('eligibility_evidence')",
            name="ck_st_evidence_file_purpose",
        ),
        sa.CheckConstraint(
            "upload_status IN ('uploading', 'ready', 'upload_failed', 'reconciliation_required')",
            name="ck_st_evidence_file_status",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_st_evidence_file_size_positive"),
        sa.CheckConstraint("length(content_sha256) = 64", name="ck_st_evidence_file_sha256_length"),
        sa.ForeignKeyConstraint(["org_id"], ["organization.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["learner_profile_id"], ["st_learner_profile.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["user.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_st_evidence_file_uuid", "st_evidence_file", ["evidence_uuid"], unique=True)
    op.create_index(
        "ix_st_evidence_file_org_learner",
        "st_evidence_file",
        ["org_id", "learner_profile_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_st_evidence_file_org_learner", table_name="st_evidence_file")
    op.drop_index("ix_st_evidence_file_uuid", table_name="st_evidence_file")
    op.drop_table("st_evidence_file")
