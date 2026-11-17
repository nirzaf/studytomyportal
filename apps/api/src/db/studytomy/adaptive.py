"""Versioned curriculum definitions and links to native lesson revisions."""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from src.db.studytomy.governance import _utcnow


class CurriculumRecord(SQLModel):
    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_type=BigInteger().with_variant(Integer, "sqlite"),
    )
    org_id: int = Field(foreign_key="organization.id", ondelete="RESTRICT")
    created_at: datetime = Field(
        default_factory=_utcnow, sa_type=DateTime(timezone=True)
    )


class StudyCurriculumRevision(CurriculumRecord, table=True):
    __tablename__ = "st_curriculum_revision"
    __table_args__ = (
        UniqueConstraint("uuid", name="uq_st_curriculum_revision_uuid"),
        UniqueConstraint("org_id", "id", name="uq_st_curriculum_revision_parent"),
        UniqueConstraint(
            "org_id", "curriculum_key", "revision", name="uq_st_curriculum_revision"
        ),
        CheckConstraint(
            "revision > 0 AND version > 0", name="ck_st_curriculum_version"
        ),
        CheckConstraint(
            "(status = 'draft' AND reviewer_id IS NULL AND approved_at IS NULL "
            "AND content_digest IS NULL) OR "
            "(status IN ('published', 'retired') AND reviewer_id IS NOT NULL "
            "AND approved_at IS NOT NULL AND content_digest IS NOT NULL "
            "AND length(content_digest) = 64 AND length(rights_reference) > 0)",
            name="ck_st_curriculum_review",
        ),
    )

    uuid: str = Field(default_factory=lambda: f"curriculum_{uuid4()}", max_length=64)
    curriculum_key: str = Field(max_length=100)
    owner_course_uuid: str | None = Field(default=None, max_length=255)
    revision: int = Field(default=1)
    version: int = Field(default=1)
    status: str = Field(default="draft", max_length=12)
    subject: str = Field(max_length=120)
    level: str = Field(max_length=80)
    language: str = Field(max_length=32)
    jurisdiction: str | None = Field(default=None, max_length=120)
    source_reference: str = Field(max_length=500)
    rights_reference: str = Field(max_length=500)
    reviewer_id: int | None = Field(
        default=None, foreign_key="user.id", ondelete="RESTRICT"
    )
    approved_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    content_digest: str | None = Field(default=None, max_length=64)
    policy_version_ids: list[int] = Field(default_factory=list, sa_type=JSON)
    updated_at: datetime = Field(
        default_factory=_utcnow, sa_type=DateTime(timezone=True)
    )


class StudyConcept(CurriculumRecord, table=True):
    __tablename__ = "st_concept"
    __table_args__ = (
        ForeignKeyConstraint(
            ("org_id", "curriculum_revision_id"),
            ("st_curriculum_revision.org_id", "st_curriculum_revision.id"),
            name="fk_st_concept_curriculum",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("uuid", name="uq_st_concept_uuid"),
        UniqueConstraint(
            "org_id", "curriculum_revision_id", "id", name="uq_st_concept_parent"
        ),
        UniqueConstraint("curriculum_revision_id", "code", name="uq_st_concept_code"),
        CheckConstraint("sequence >= 0", name="ck_st_concept_sequence"),
    )

    uuid: str = Field(default_factory=lambda: f"concept_{uuid4()}", max_length=64)
    curriculum_revision_id: int = Field(sa_type=BigInteger)
    code: str = Field(max_length=100)
    title: str = Field(max_length=200)
    objective: str = Field(max_length=2000)
    sequence: int = Field(default=0)
    archived: bool = Field(default=False)


class StudyConceptPrerequisite(CurriculumRecord, table=True):
    __tablename__ = "st_concept_prerequisite"
    __table_args__ = (
        ForeignKeyConstraint(
            ("org_id", "curriculum_revision_id", "concept_id"),
            ("st_concept.org_id", "st_concept.curriculum_revision_id", "st_concept.id"),
            name="fk_st_prerequisite_concept",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("org_id", "curriculum_revision_id", "prerequisite_id"),
            ("st_concept.org_id", "st_concept.curriculum_revision_id", "st_concept.id"),
            name="fk_st_prerequisite_target",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("concept_id", "prerequisite_id", name="uq_st_concept_edge"),
        UniqueConstraint("uuid", name="uq_st_concept_prerequisite_uuid"),
        Index("ix_st_prerequisite_target", "prerequisite_id"),
        CheckConstraint(
            "concept_id <> prerequisite_id", name="ck_st_concept_edge_no_self"
        ),
    )

    uuid: str = Field(default_factory=lambda: f"edge_{uuid4()}", max_length=64)
    curriculum_revision_id: int = Field(sa_type=BigInteger)
    concept_id: int = Field(sa_type=BigInteger)
    prerequisite_id: int = Field(sa_type=BigInteger)


class StudyConceptActivity(CurriculumRecord, table=True):
    __tablename__ = "st_concept_activity"
    __table_args__ = (
        ForeignKeyConstraint(
            ("org_id", "curriculum_revision_id", "concept_id"),
            ("st_concept.org_id", "st_concept.curriculum_revision_id", "st_concept.id"),
            name="fk_st_concept_activity_concept",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("uuid", name="uq_st_concept_activity_uuid"),
        UniqueConstraint(
            "concept_id", "activity_uuid", "usage", name="uq_st_concept_activity"
        ),
        Index("ix_st_concept_activity_lesson", "org_id", "activity_uuid"),
        CheckConstraint(
            "usage IN ('teach', 'practice', 'review')",
            name="ck_st_concept_activity_usage",
        ),
        CheckConstraint(
            "activity_version >= 1 AND length(activity_digest) = 64",
            name="ck_st_concept_activity_revision",
        ),
    )

    uuid: str = Field(default_factory=lambda: f"concept_map_{uuid4()}", max_length=64)
    curriculum_revision_id: int = Field(sa_type=BigInteger)
    concept_id: int = Field(sa_type=BigInteger)
    # These identifiers are historical snapshots. Validate the current pair
    # when writing the mapping without blocking native course deletion later.
    course_id: int
    course_uuid: str = Field(max_length=255)
    activity_id: int
    activity_uuid: str = Field(max_length=89)
    activity_version: int
    activity_digest: str = Field(max_length=64)
    usage: str = Field(max_length=12)
    review_reference: str | None = Field(default=None, max_length=500)
    archived: bool = Field(default=False)


class CurriculumRevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    uuid: str
    curriculum_key: str
    owner_course_uuid: str | None
    revision: int
    version: int
    subject: str
    level: str
    language: str
    jurisdiction: str | None


class CurriculumDraftRead(CurriculumRevisionRead):
    status: Literal["draft"]


class CurriculumReviewedRead(CurriculumRevisionRead):
    status: Literal["published", "retired"]
    approved_at: datetime
    content_digest: str

    @field_validator("approved_at")
    @classmethod
    def normalize_approved_at(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
