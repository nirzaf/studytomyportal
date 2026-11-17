"""Versioned definition of an independently reviewed diagnostic."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    UniqueConstraint,
)
from sqlmodel import Field

from src.db.studytomy.adaptive import CurriculumRecord
from src.db.studytomy.governance import _utcnow


class StudyDiagnosticRevision(CurriculumRecord, table=True):
    __tablename__ = "st_diagnostic_revision"
    __table_args__ = (
        ForeignKeyConstraint(
            ("org_id", "curriculum_revision_id"),
            ("st_curriculum_revision.org_id", "st_curriculum_revision.id"),
            name="fk_st_diagnostic_curriculum",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("uuid", name="uq_st_diagnostic_uuid"),
        UniqueConstraint("org_id", "curriculum_revision_id", "id", "blueprint_uuid",
                         name="uq_st_diagnostic_attempt_binding"),
        UniqueConstraint(
            "org_id", "blueprint_uuid", "revision", name="uq_st_diagnostic_revision"
        ),
        CheckConstraint(
            "revision >= 1 AND version >= 1", name="ck_st_diagnostic_version"
        ),
        CheckConstraint(
            "(status = 'draft' AND reviewer_id IS NULL AND approved_at IS NULL AND content_digest IS NULL)"
            " OR (status IN ('published', 'retired') AND reviewer_id IS NOT NULL AND approved_at IS NOT NULL"
            " AND content_digest IS NOT NULL AND length(content_digest) = 64)",
            name="ck_st_diagnostic_review",
        ),
    )

    uuid: str = Field(default_factory=lambda: f"diagnostic_{uuid4()}", max_length=64)
    blueprint_uuid: str = Field(max_length=64)
    curriculum_revision_id: int = Field(sa_type=BigInteger)
    name: str = Field(max_length=120)
    revision: int
    version: int = 1
    status: str = Field(default="draft", max_length=12)
    selector_version: str = Field(max_length=40)
    mastery_policy_uuid: str = Field(max_length=100)
    manifest: dict = Field(sa_type=JSON)
    reviewer_id: int | None = Field(
        default=None, foreign_key="user.id", ondelete="RESTRICT"
    )
    approved_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    content_digest: str | None = Field(default=None, max_length=64)
    policy_version_ids: list[int] = Field(default_factory=list, sa_type=JSON)
    updated_at: datetime = Field(
        default_factory=_utcnow, sa_type=DateTime(timezone=True)
    )
