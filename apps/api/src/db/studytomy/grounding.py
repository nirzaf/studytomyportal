"""Private, versioned sources for drafting in managed courses."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, Column, DateTime, LargeBinary, UniqueConstraint
from sqlmodel import Field, SQLModel


class StudyDraftMaterial(SQLModel, table=True):
    __tablename__ = "st_draft_material"
    __table_args__ = (
        UniqueConstraint("course_id", "activity_id", "sha256", name="uq_st_draft_material_lesson_digest"),
    )

    id: int | None = Field(default=None, primary_key=True)
    source_uuid: str = Field(default_factory=lambda: str(uuid4()), max_length=36, unique=True)
    course_id: int = Field(foreign_key="course.id", ondelete="RESTRICT")
    activity_id: int = Field(foreign_key="activity.id", ondelete="RESTRICT")
    uploaded_by_user_id: int = Field(foreign_key="user.id", ondelete="RESTRICT")
    label: str = Field(max_length=160)
    sha256: str = Field(max_length=64)
    original_pdf: bytes = Field(sa_column=Column(LargeBinary, nullable=False, info={"private": True}))
    pages: list[dict] = Field(sa_column=Column(JSON, nullable=False, info={"private": True}))
    source_provenance: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    revoked_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class StudyGroundedLessonEvidence(SQLModel, table=True):
    __tablename__ = "st_grounded_lesson_evidence"

    activity_id: int = Field(primary_key=True, foreign_key="activity.id", ondelete="RESTRICT")
    activity_version: int = Field(primary_key=True)
    content_sha256: str = Field(max_length=64)
    citations: list[dict] = Field(sa_column=Column(JSON, nullable=False, info={"private": True}))


class StudyGroundedQuizEvidence(SQLModel, table=True):
    __tablename__ = "st_grounded_quiz_evidence"

    task_uuid: str = Field(primary_key=True, max_length=100)
    content_sha256: str = Field(max_length=64)
    citations: dict = Field(sa_column=Column(JSON, nullable=False, info={"private": True}))
