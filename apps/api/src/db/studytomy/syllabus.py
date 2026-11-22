"""Organization-scoped, versioned syllabus documents and reviewed evidence."""

from datetime import UTC, datetime
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    Text,
    UniqueConstraint,
    text,
)
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(UTC)


class StudySyllabusSource(SQLModel, table=True):
    """A logical organization-owned source; each upload is a separate version."""

    __tablename__ = "st_syllabus_source"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_st_syllabus_source_org_id"),
        UniqueConstraint("org_id", "source_uuid", name="uq_st_syllabus_source_uuid"),
        Index("ix_st_syllabus_source_org_title", "org_id", "title"),
    )

    id: int | None = Field(default=None, sa_type=BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    org_id: int = Field(sa_column=Column(Integer, ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False))
    source_uuid: str = Field(default_factory=lambda: f"syllabus_{uuid4()}", max_length=64)
    title: str = Field(max_length=200)
    created_by_user_id: int = Field(sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class StudySyllabusSourceVersion(SQLModel, table=True):
    """Immutable PDF bytes and extraction/index state for one source revision."""

    __tablename__ = "st_syllabus_source_version"
    __table_args__ = (
        ForeignKeyConstraint(
            ("org_id", "source_id"),
            ("st_syllabus_source.org_id", "st_syllabus_source.id"),
            name="fk_st_syllabus_version_source",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("org_id", "id", name="uq_st_syllabus_version_org_id"),
        UniqueConstraint("source_id", "version_number", name="uq_st_syllabus_version_number"),
        UniqueConstraint("org_id", "content_sha256", name="uq_st_syllabus_org_digest"),
        UniqueConstraint("version_uuid", name="uq_st_syllabus_version_uuid"),
        Index(
            "uq_st_syllabus_approved_version",
            "source_id",
            unique=True,
            postgresql_where=text("review_status = 'approved'"),
            sqlite_where=text("review_status = 'approved'"),
        ),
        Index("ix_st_syllabus_version_search", "org_id", "jurisdiction", "grade", "subject", "syllabus_edition"),
        CheckConstraint("version_number > 0", name="ck_st_syllabus_version_number"),
        CheckConstraint("length(content_sha256) = 64", name="ck_st_syllabus_digest"),
        CheckConstraint("size_bytes > 0 AND size_bytes <= 4194304", name="ck_st_syllabus_size"),
        CheckConstraint(
            "page_count BETWEEN 0 AND 200 AND (page_count > 0 OR ingestion_status = 'failed')",
            name="ck_st_syllabus_pages",
        ),
        CheckConstraint(
            "source_type IN ('syllabus', 'textbook', 'resource_book', 'supplementary', 'teacher_guide')",
            name="ck_st_syllabus_source_type",
        ),
        CheckConstraint(
            "rights_status IN ('unverified', 'verified_permitted', 'restricted')",
            name="ck_st_syllabus_rights_status",
        ),
        CheckConstraint(
            "ingestion_status IN ('processing', 'ready', 'failed')",
            name="ck_st_syllabus_ingestion_status",
        ),
        CheckConstraint(
            "review_status IN ('pending_review', 'approved', 'rejected', 'revoked', 'superseded')",
            name="ck_st_syllabus_review_status",
        ),
        CheckConstraint(
            "review_status <> 'approved' OR (page_count > 0 AND ingestion_status = 'ready' AND rights_status = 'verified_permitted' "
            "AND length(rights_reference) > 0 AND reviewed_by_user_id IS NOT NULL "
            "AND reviewed_by_user_id <> uploaded_by_user_id AND approved_at IS NOT NULL)",
            name="ck_st_syllabus_approval_evidence",
        ),
    )

    id: int | None = Field(default=None, sa_type=BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    org_id: int = Field(sa_column=Column(Integer, nullable=False))
    source_id: int = Field(sa_type=BigInteger)
    version_uuid: str = Field(default_factory=lambda: f"syllabus_version_{uuid4()}", max_length=80)
    version_number: int = Field(default=1)
    title: str = Field(max_length=200)
    extracted_pages: list[dict] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    jurisdiction: str = Field(max_length=120)
    grade: str = Field(max_length=32)
    subject: str = Field(max_length=120)
    language: str = Field(max_length=32)
    syllabus_edition: str = Field(max_length=64)
    effective_cohort: str = Field(max_length=64)
    publisher: str = Field(max_length=200)
    source_type: str = Field(max_length=24)
    source_revision: str = Field(max_length=120)
    rights_status: str = Field(default="unverified", max_length=24)
    rights_reference: str = Field(default="", max_length=500)
    original_filename: str = Field(max_length=180)
    storage_reference: str = Field(max_length=500)
    content_sha256: str = Field(max_length=64)
    size_bytes: int
    page_count: int
    ingestion_status: str = Field(default="processing", max_length=16)
    ingestion_error_code: str | None = Field(default=None, max_length=40)
    ingestion_error_message: str | None = Field(default=None, max_length=500)
    ingestion_attempts: int = Field(default=1)
    review_status: str = Field(default="pending_review", max_length=20)
    uploaded_by_user_id: int = Field(sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False))
    reviewed_by_user_id: int | None = Field(default=None, sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT")))
    review_evidence: str | None = Field(default=None, max_length=500)
    approved_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    revoked_by_user_id: int | None = Field(default=None, sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT")))
    revocation_evidence: str | None = Field(default=None, max_length=500)
    revoked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
    updated_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class StudySyllabusChunk(SQLModel, table=True):
    """Page-preserving chunks with the provider provenance used to index them."""

    __tablename__ = "st_syllabus_chunk"
    __table_args__ = (
        ForeignKeyConstraint(
            ("org_id", "source_version_id"),
            ("st_syllabus_source_version.org_id", "st_syllabus_source_version.id"),
            name="fk_st_syllabus_chunk_version",
            ondelete="CASCADE",
        ),
        UniqueConstraint("source_version_id", "page_number", "chunk_index", name="uq_st_syllabus_chunk_position"),
        CheckConstraint("page_number >= 1 AND chunk_index >= 0", name="ck_st_syllabus_chunk_locator"),
        Index("ix_st_syllabus_chunk_org_page", "org_id", "source_version_id", "page_number"),
        Index("ix_st_syllabus_chunk_embedding", "embedding", postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"}),
        Index(
            "ix_st_syllabus_chunk_fts",
            text("to_tsvector('simple', chunk_text)"),
            postgresql_using="gin",
        ),
    )

    id: int | None = Field(default=None, sa_type=BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    org_id: int = Field(sa_column=Column(Integer, nullable=False))
    source_version_id: int = Field(sa_type=BigInteger)
    page_number: int
    chunk_index: int
    chunk_text: str = Field(sa_type=Text)
    text_sha256: str = Field(max_length=64)
    embedding_space: str = Field(max_length=64)
    embedding: list[float] = Field(sa_column=Column(Vector(768), nullable=False))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))


class StudySyllabusConceptMapping(SQLModel, table=True):
    """Human-reviewed page evidence linking a source version to a curriculum concept."""

    __tablename__ = "st_syllabus_concept_mapping"
    __table_args__ = (
        ForeignKeyConstraint(
            ("org_id", "source_version_id"),
            ("st_syllabus_source_version.org_id", "st_syllabus_source_version.id"),
            name="fk_st_syllabus_mapping_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("org_id", "curriculum_revision_id"),
            ("st_curriculum_revision.org_id", "st_curriculum_revision.id"),
            name="fk_st_syllabus_mapping_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("org_id", "curriculum_revision_id", "concept_id"),
            ("st_concept.org_id", "st_concept.curriculum_revision_id", "st_concept.id"),
            name="fk_st_syllabus_mapping_concept",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("mapping_uuid", name="uq_st_syllabus_mapping_uuid"),
        Index(
            "uq_st_syllabus_mapping_active_evidence",
            "source_version_id", "curriculum_revision_id", "concept_id", "page_start", "page_end", "evidence_kind",
            unique=True,
            postgresql_where=text("status IN ('pending_review', 'approved')"),
            sqlite_where=text("status IN ('pending_review', 'approved')"),
        ),
        CheckConstraint("page_start >= 1 AND page_end >= page_start", name="ck_st_syllabus_mapping_pages"),
        CheckConstraint("evidence_kind IN ('official', 'supplementary')", name="ck_st_syllabus_mapping_kind"),
        CheckConstraint("status IN ('pending_review', 'approved', 'rejected', 'revoked')", name="ck_st_syllabus_mapping_status"),
        CheckConstraint(
            "status <> 'approved' OR (reviewed_by_user_id IS NOT NULL "
            "AND reviewed_by_user_id <> created_by_user_id AND approved_at IS NOT NULL)",
            name="ck_st_syllabus_mapping_review",
        ),
        Index("ix_st_syllabus_mapping_lookup", "org_id", "curriculum_revision_id", "concept_id", "status"),
    )

    id: int | None = Field(default=None, sa_type=BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    org_id: int = Field(sa_column=Column(Integer, nullable=False))
    mapping_uuid: str = Field(default_factory=lambda: f"syllabus_map_{uuid4()}", max_length=80)
    source_version_id: int = Field(sa_type=BigInteger)
    curriculum_revision_id: int = Field(sa_type=BigInteger)
    concept_id: int = Field(sa_type=BigInteger)
    page_start: int
    page_end: int
    evidence_quote: str = Field(sa_type=Text)
    evidence_kind: str = Field(max_length=16)
    status: str = Field(default="pending_review", max_length=20)
    created_by_user_id: int = Field(sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False))
    reviewed_by_user_id: int | None = Field(default=None, sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT")))
    review_evidence: str | None = Field(default=None, max_length=500)
    approved_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    revoked_by_user_id: int | None = Field(default=None, sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT")))
    revocation_evidence: str | None = Field(default=None, max_length=500)
    revoked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False))
