"""Private, revisioned snapshots of reviewed-candidate assessment items."""

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


class StudyItemRevision(CurriculumRecord, table=True):
    __tablename__ = "st_item_revision"
    __table_args__ = (
        ForeignKeyConstraint(
            ("org_id", "curriculum_revision_id", "concept_id"),
            ("st_concept.org_id", "st_concept.curriculum_revision_id", "st_concept.id"),
            name="fk_st_item_concept",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("uuid", name="uq_st_item_uuid"),
        UniqueConstraint("org_id", "curriculum_revision_id", "concept_id", "id", "item_family_uuid",
                         name="uq_st_item_issue_binding"),
        UniqueConstraint(
            "org_id", "item_family_uuid", "revision", name="uq_st_item_family_revision"
        ),
        CheckConstraint(
            "revision > 0 AND version > 0 AND difficulty BETWEEN 1 AND 5",
            name="ck_st_item_version",
        ),
        CheckConstraint("kind IN ('single_choice', 'number')", name="ck_st_item_kind"),
        CheckConstraint("length(source_digest) = 64", name="ck_st_item_source_digest"),
        CheckConstraint(
            "(source_exposure IN ('possible_native_reveal', 'unverified_native_history')"
            " AND assignment_uuid IS NOT NULL AND source_task_uuid IS NOT NULL)"
            " OR (source_exposure = 'private_original' AND assignment_uuid IS NULL"
            " AND source_task_uuid IS NULL AND source_question_uuid IS NULL)",
            name="ck_st_item_source_provenance",
        ),
        CheckConstraint(
            "(status = 'draft' AND reviewer_id IS NULL AND approved_at IS NULL AND content_digest IS NULL)"
            " OR (status IN ('published', 'retired') AND reviewer_id IS NOT NULL AND approved_at IS NOT NULL"
            " AND content_digest IS NOT NULL AND length(content_digest) = 64)",
            name="ck_st_item_review",
        ),
    )

    uuid: str = Field(default_factory=lambda: f"item_{uuid4()}", max_length=64)
    item_family_uuid: str = Field(max_length=64)
    curriculum_revision_id: int = Field(sa_type=BigInteger)
    concept_id: int = Field(sa_type=BigInteger)
    revision: int
    version: int = 1
    status: str = Field(default="draft", max_length=12)
    kind: str = Field(max_length=20)
    difficulty: int
    course_uuid: str = Field(max_length=255)
    assignment_uuid: str | None = Field(default=None, max_length=100)
    source_task_uuid: str | None = Field(default=None, max_length=100)
    source_question_uuid: str | None = Field(default=None, max_length=100)
    source_digest: str = Field(max_length=64)
    source_exposure: str = Field(max_length=32)
    evaluator_version: str = Field(max_length=40)
    rights_reference: str = Field(max_length=500)
    public_question: dict = Field(sa_type=JSON)
    private_answer: dict = Field(sa_type=JSON)
    hint_ladder: list[str] = Field(default_factory=list, sa_type=JSON)
    error_codes: dict[str, str] = Field(default_factory=dict, sa_type=JSON)
    reviewer_id: int | None = Field(
        default=None, foreign_key="user.id", ondelete="RESTRICT"
    )
    approved_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    content_digest: str | None = Field(default=None, max_length=64)
    policy_version_ids: list[int] = Field(default_factory=list, sa_type=JSON)
    updated_at: datetime = Field(
        default_factory=_utcnow, sa_type=DateTime(timezone=True)
    )
