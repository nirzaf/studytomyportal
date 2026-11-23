"""Durable source and evaluation basis for ungraded lesson practice."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Index,
    String,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from src.db.studytomy.governance import _utcnow


class StudyLearningSurface(SQLModel, table=True):
    __tablename__ = "st_learning_surface"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", "request_uuid", name="uq_st_learning_surface_request"),
        Index("ix_st_learning_surface_retention_expiry", "org_id", "retention_policy_id", "retention_expires_at"),
        CheckConstraint(
            "(retention_policy_id IS NULL AND retention_category IS NULL AND retention_expires_at IS NULL) OR "
            "(retention_policy_id IS NOT NULL AND retention_category = 'lesson_visual_practice' "
            "AND retention_expires_at > created_at)",
            name="ck_st_learning_surface_retention_binding",
        ),
    )

    surface_uuid: str = Field(sa_column=Column(String(36), primary_key=True))
    org_id: int = Field(
        sa_column=Column(Integer, ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False),
    )
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False),
    )
    request_uuid: str = Field(sa_column=Column(String(36), nullable=False))
    # Preserve the evidence even if the lesson is removed. Recovery checks the
    # current lesson and its owning course before it projects the snapshot.
    course_uuid: str = Field(sa_column=Column(String(255), nullable=False))
    activity_uuid: str = Field(sa_column=Column(String(89), nullable=False))
    source_identity: str = Field(sa_column=Column(String(64), nullable=False))
    presentation: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    evaluation_basis: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON(none_as_null=True), nullable=True),
    )
    retention_policy_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("st_policy_version.id", ondelete="RESTRICT"), nullable=True),
    )
    retention_category: str | None = Field(default=None, sa_column=Column(String(64), nullable=True))
    retention_expires_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class StudyLearningAction(SQLModel, table=True):
    """A new committed revision and its private evidence."""

    __tablename__ = "st_learning_action"
    __table_args__ = (
        UniqueConstraint("surface_uuid", "revision", name="uq_st_learning_action_revision"),
        CheckConstraint("revision > 1", name="ck_st_learning_action_revision"),
        CheckConstraint(
            "action IN ('submit_answer', 'request_hint', 'retry_practice', 'next_question')",
            name="ck_st_learning_action_kind",
        ),
        CheckConstraint(
            "(action = 'submit_answer' AND answer IS NOT NULL AND assisted IS NOT NULL) OR "
            "(action <> 'submit_answer' AND answer IS NULL AND assisted IS NULL)",
            name="ck_st_learning_action_answer_evidence",
        ),
        CheckConstraint(
            "(retention_policy_id IS NULL AND retention_category IS NULL AND retention_expires_at IS NULL) OR "
            "(retention_policy_id IS NOT NULL AND retention_category = 'lesson_visual_practice' "
            "AND retention_expires_at IS NOT NULL)",
            name="ck_st_learning_action_retention_binding",
        ),
    )

    surface_uuid: str = Field(sa_column=Column(
        String(36), ForeignKey("st_learning_surface.surface_uuid", ondelete="RESTRICT"), primary_key=True,
    ))
    request_uuid: str = Field(sa_column=Column(String(36), primary_key=True))
    request_hash: str = Field(sa_column=Column(String(64), nullable=False))
    revision: int = Field(sa_column=Column(Integer, nullable=False))
    action: str = Field(sa_column=Column(String(20), nullable=False))
    answer: str | None = Field(default=None, sa_column=Column(String(48)))
    assisted: bool | None = Field(default=None, sa_column=Column(Boolean))
    presentation: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    evaluation_basis: dict[str, Any] | None = Field(
        default=None, sa_column=Column(JSON(none_as_null=True)),
    )
    retention_policy_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("st_policy_version.id", ondelete="RESTRICT"), nullable=True),
    )
    retention_category: str | None = Field(default=None, sa_column=Column(String(64), nullable=True))
    retention_expires_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(DateTime(timezone=True), nullable=False),
    )
