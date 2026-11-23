"""Add organization-scoped, reviewed syllabus PDF sources."""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "d9f74c2a81e6"
down_revision = "c5d1e8f2a904"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "st_syllabus_source",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("source_uuid", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("org_id", "id", name="uq_st_syllabus_source_org_id"),
        sa.UniqueConstraint("org_id", "source_uuid", name="uq_st_syllabus_source_uuid"),
    )
    op.create_index("ix_st_syllabus_source_org_title", "st_syllabus_source", ["org_id", "title"])

    op.create_table(
        "st_syllabus_source_version",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("version_uuid", sa.String(length=80), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("extracted_pages", sa.JSON(), nullable=False),
        sa.Column("jurisdiction", sa.String(length=120), nullable=False),
        sa.Column("grade", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=120), nullable=False),
        sa.Column("language", sa.String(length=32), nullable=False),
        sa.Column("syllabus_edition", sa.String(length=64), nullable=False),
        sa.Column("effective_cohort", sa.String(length=64), nullable=False),
        sa.Column("publisher", sa.String(length=200), nullable=False),
        sa.Column("source_type", sa.String(length=24), nullable=False),
        sa.Column("source_revision", sa.String(length=120), nullable=False),
        sa.Column("rights_status", sa.String(length=24), server_default="unverified", nullable=False),
        sa.Column("rights_reference", sa.String(length=500), server_default="", nullable=False),
        sa.Column("original_filename", sa.String(length=180), nullable=False),
        sa.Column("storage_reference", sa.String(length=500), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("ingestion_status", sa.String(length=16), server_default="processing", nullable=False),
        sa.Column("ingestion_error_code", sa.String(length=40), nullable=True),
        sa.Column("ingestion_error_message", sa.String(length=500), nullable=True),
        sa.Column("ingestion_attempts", sa.Integer(), server_default="1", nullable=False),
        sa.Column("review_status", sa.String(length=20), server_default="pending_review", nullable=False),
        sa.Column("uploaded_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("review_evidence", sa.String(length=500), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "source_id"],
            ["st_syllabus_source.org_id", "st_syllabus_source.id"],
            name="fk_st_syllabus_version_source",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("org_id", "id", name="uq_st_syllabus_version_org_id"),
        sa.UniqueConstraint("source_id", "version_number", name="uq_st_syllabus_version_number"),
        sa.UniqueConstraint("org_id", "content_sha256", name="uq_st_syllabus_org_digest"),
        sa.UniqueConstraint("version_uuid", name="uq_st_syllabus_version_uuid"),
        sa.CheckConstraint("version_number > 0", name="ck_st_syllabus_version_number"),
        sa.CheckConstraint("length(content_sha256) = 64", name="ck_st_syllabus_digest"),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 4194304", name="ck_st_syllabus_size"),
        sa.CheckConstraint(
            "page_count BETWEEN 0 AND 200 AND (page_count > 0 OR ingestion_status = 'failed')",
            name="ck_st_syllabus_pages",
        ),
        sa.CheckConstraint(
            "source_type IN ('syllabus', 'textbook', 'resource_book', 'supplementary', 'teacher_guide')",
            name="ck_st_syllabus_source_type",
        ),
        sa.CheckConstraint(
            "rights_status IN ('unverified', 'verified_permitted', 'restricted')",
            name="ck_st_syllabus_rights_status",
        ),
        sa.CheckConstraint(
            "ingestion_status IN ('processing', 'ready', 'failed')",
            name="ck_st_syllabus_ingestion_status",
        ),
        sa.CheckConstraint(
            "review_status IN ('pending_review', 'approved', 'rejected', 'revoked', 'superseded')",
            name="ck_st_syllabus_review_status",
        ),
        sa.CheckConstraint(
            "review_status <> 'approved' OR (page_count > 0 AND ingestion_status = 'ready' AND rights_status = 'verified_permitted' "
            "AND length(rights_reference) > 0 AND reviewed_by_user_id IS NOT NULL "
            "AND reviewed_by_user_id <> uploaded_by_user_id AND approved_at IS NOT NULL)",
            name="ck_st_syllabus_approval_evidence",
        ),
    )
    op.create_index(
        "uq_st_syllabus_approved_version", "st_syllabus_source_version", ["source_id"],
        unique=True, postgresql_where=sa.text("review_status = 'approved'"),
    )
    op.create_index(
        "ix_st_syllabus_version_search", "st_syllabus_source_version",
        ["org_id", "jurisdiction", "grade", "subject", "syllabus_edition"],
    )
    op.execute(
        """
        CREATE FUNCTION protect_st_syllabus_version_identity() RETURNS trigger AS $$
        BEGIN
            IF NEW.extracted_pages::text IS DISTINCT FROM OLD.extracted_pages::text OR ROW(
                NEW.org_id, NEW.source_id, NEW.version_uuid, NEW.version_number, NEW.title,
                NEW.jurisdiction, NEW.grade, NEW.subject, NEW.language,
                NEW.syllabus_edition, NEW.effective_cohort, NEW.publisher, NEW.source_type,
                NEW.source_revision, NEW.original_filename,
                NEW.storage_reference, NEW.content_sha256, NEW.size_bytes, NEW.page_count,
                NEW.uploaded_by_user_id, NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.org_id, OLD.source_id, OLD.version_uuid, OLD.version_number, OLD.title,
                OLD.jurisdiction, OLD.grade, OLD.subject, OLD.language,
                OLD.syllabus_edition, OLD.effective_cohort, OLD.publisher, OLD.source_type,
                OLD.source_revision, OLD.original_filename,
                OLD.storage_reference, OLD.content_sha256, OLD.size_bytes, OLD.page_count,
                OLD.uploaded_by_user_id, OLD.created_at
            ) THEN
                RAISE EXCEPTION 'syllabus source version provenance is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER st_syllabus_version_identity_immutable "
        "BEFORE UPDATE ON st_syllabus_source_version FOR EACH ROW "
        "EXECUTE FUNCTION protect_st_syllabus_version_identity()"
    )

    op.create_table(
        "st_syllabus_chunk",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("source_version_id", sa.BigInteger(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(length=64), nullable=False),
        sa.Column("embedding_space", sa.String(length=64), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "source_version_id"],
            ["st_syllabus_source_version.org_id", "st_syllabus_source_version.id"],
            name="fk_st_syllabus_chunk_version",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("source_version_id", "page_number", "chunk_index", name="uq_st_syllabus_chunk_position"),
        sa.CheckConstraint("page_number >= 1 AND chunk_index >= 0", name="ck_st_syllabus_chunk_locator"),
    )
    op.create_index("ix_st_syllabus_chunk_org_page", "st_syllabus_chunk", ["org_id", "source_version_id", "page_number"])
    op.create_index(
        "ix_st_syllabus_chunk_embedding", "st_syllabus_chunk", ["embedding"],
        postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_st_syllabus_chunk_fts", "st_syllabus_chunk",
        [sa.text("to_tsvector('simple', chunk_text)")], postgresql_using="gin",
    )

    op.create_table(
        "st_syllabus_concept_mapping",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("mapping_uuid", sa.String(length=80), nullable=False),
        sa.Column("source_version_id", sa.BigInteger(), nullable=False),
        sa.Column("curriculum_revision_id", sa.BigInteger(), nullable=False),
        sa.Column("concept_id", sa.BigInteger(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("evidence_quote", sa.Text(), nullable=False),
        sa.Column("evidence_kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="pending_review", nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("review_evidence", sa.String(length=500), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id", "source_version_id"],
            ["st_syllabus_source_version.org_id", "st_syllabus_source_version.id"],
            name="fk_st_syllabus_mapping_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "curriculum_revision_id"],
            ["st_curriculum_revision.org_id", "st_curriculum_revision.id"],
            name="fk_st_syllabus_mapping_curriculum",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["org_id", "curriculum_revision_id", "concept_id"],
            ["st_concept.org_id", "st_concept.curriculum_revision_id", "st_concept.id"],
            name="fk_st_syllabus_mapping_concept",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("mapping_uuid", name="uq_st_syllabus_mapping_uuid"),
        sa.UniqueConstraint(
            "source_version_id", "curriculum_revision_id", "concept_id", "page_start", "page_end", "evidence_kind",
            name="uq_st_syllabus_mapping_evidence",
        ),
        sa.CheckConstraint("page_start >= 1 AND page_end >= page_start", name="ck_st_syllabus_mapping_pages"),
        sa.CheckConstraint("evidence_kind IN ('official', 'supplementary')", name="ck_st_syllabus_mapping_kind"),
        sa.CheckConstraint("status IN ('pending_review', 'approved', 'rejected', 'revoked')", name="ck_st_syllabus_mapping_status"),
        sa.CheckConstraint(
            "status <> 'approved' OR (reviewed_by_user_id IS NOT NULL "
            "AND reviewed_by_user_id <> created_by_user_id AND approved_at IS NOT NULL)",
            name="ck_st_syllabus_mapping_review",
        ),
    )
    op.create_index(
        "ix_st_syllabus_mapping_lookup", "st_syllabus_concept_mapping",
        ["org_id", "curriculum_revision_id", "concept_id", "status"],
    )
    op.execute(
        """
        CREATE FUNCTION protect_st_syllabus_mapping_evidence() RETURNS trigger AS $$
        BEGIN
            IF ROW(
                NEW.org_id, NEW.mapping_uuid, NEW.source_version_id, NEW.curriculum_revision_id,
                NEW.concept_id, NEW.page_start, NEW.page_end, NEW.evidence_kind,
                NEW.evidence_quote, NEW.created_by_user_id, NEW.created_at
            ) IS DISTINCT FROM ROW(
                OLD.org_id, OLD.mapping_uuid, OLD.source_version_id, OLD.curriculum_revision_id,
                OLD.concept_id, OLD.page_start, OLD.page_end, OLD.evidence_kind,
                OLD.evidence_quote, OLD.created_by_user_id, OLD.created_at
            ) THEN
                RAISE EXCEPTION 'syllabus mapping evidence is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER st_syllabus_mapping_evidence_immutable "
        "BEFORE UPDATE ON st_syllabus_concept_mapping FOR EACH ROW "
        "EXECUTE FUNCTION protect_st_syllabus_mapping_evidence()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS st_syllabus_mapping_evidence_immutable ON st_syllabus_concept_mapping")
    op.execute("DROP FUNCTION IF EXISTS protect_st_syllabus_mapping_evidence()")
    op.execute("DROP TRIGGER IF EXISTS st_syllabus_version_identity_immutable ON st_syllabus_source_version")
    op.execute("DROP FUNCTION IF EXISTS protect_st_syllabus_version_identity()")
    op.drop_table("st_syllabus_concept_mapping")
    op.drop_table("st_syllabus_chunk")
    op.drop_table("st_syllabus_source_version")
    op.drop_table("st_syllabus_source")
