"""Durable adaptive tutor session authority."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
)
from sqlmodel import Field

from src.db.studytomy.adaptive import CurriculumRecord


class StudyTutorSession(CurriculumRecord, table=True):
    __tablename__ = "st_tutor_session"
    __table_args__ = (
        ForeignKeyConstraint(
            ("org_id", "learner_profile_id"),
            ("st_learner_profile.org_id", "st_learner_profile.id"),
            name="fk_st_tutor_learner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("org_id", "curriculum_revision_id", "concept_id"),
            ("st_concept.org_id", "st_concept.curriculum_revision_id", "st_concept.id"),
            name="fk_st_tutor_concept",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "org_id",
                "practice_attempt_id",
                "learner_profile_id",
                "curriculum_revision_id",
            ),
            (
                "st_learning_attempt.org_id",
                "st_learning_attempt.id",
                "st_learning_attempt.learner_profile_id",
                "st_learning_attempt.curriculum_revision_id",
            ),
            name="fk_st_tutor_practice",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("uuid", name="uq_st_tutor_session_uuid"),
        UniqueConstraint("org_id", "id", name="uq_st_tutor_session_org_id"),
        CheckConstraint(
            "version >= 1 AND step >= 0 AND length(activity_digest) = 64 AND mode = 'guided' AND "
            "(closed_at IS NULL OR closed_at >= created_at)",
            name="ck_st_tutor_state",
        ),
    )

    uuid: str = Field(default_factory=lambda: f"tutor_{uuid4()}", max_length=64)
    learner_profile_id: int = Field(sa_type=BigInteger)
    curriculum_revision_id: int = Field(sa_type=BigInteger)
    concept_id: int = Field(sa_type=BigInteger)
    activity_uuid: str = Field(max_length=89)
    activity_digest: str = Field(max_length=64)
    course_uuid: str = Field(max_length=255)
    practice_attempt_id: int | None = Field(default=None, sa_type=BigInteger)
    eligibility_decision_id: int = Field(sa_type=BigInteger)
    policy_version_ids: list[int] = Field(sa_type=JSON)
    mode: str = Field(default="guided", max_length=16)
    step: int = 0
    version: int = 1
    closed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class StudyTutorTurn(CurriculumRecord, table=True):
    __tablename__ = "st_tutor_turn"
    __table_args__ = (
        ForeignKeyConstraint(
            ("org_id", "session_id"),
            ("st_tutor_session.org_id", "st_tutor_session.id"),
            name="fk_st_tutor_turn_session",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "session_id", "operation_uuid", name="uq_st_tutor_turn_operation"
        ),
        CheckConstraint(
            "(feedback_issue IS NULL AND feedback_at IS NULL AND feedback_note IS NULL) OR "
            "(feedback_issue IS NOT NULL AND feedback_issue IN ('inaccurate','unclear','unsafe') "
            "AND feedback_at IS NOT NULL AND state = 'completed')",
            name="ck_st_tutor_turn_feedback",
        ),
        Index("ix_st_tutor_turn_session_created", "session_id", "created_at"),
        CheckConstraint(
            "state IN ('running', 'completed', 'failed', 'reconciliation_required', 'cancelled') "
            "AND action IN ('explain', 'hint', 'check_understanding') "
            "AND requested_version >= 1 AND length(request_digest) = 64 "
            "AND (credit_used IS NULL OR credit_used >= 0) "
            "AND (state = 'completed' OR result IS NULL)",
            name="ck_st_tutor_turn_state",
        ),
    )

    session_id: int = Field(sa_type=BigInteger)
    operation_uuid: str = Field(max_length=36)
    request_digest: str = Field(max_length=64)
    action: str = Field(max_length=24)
    state: str = Field(max_length=32)
    requested_version: int
    credit_used: int | None = None
    result: dict | None = Field(default=None, sa_type=JSON(none_as_null=True))
    result_expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    finished_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    feedback_issue: str | None = Field(default=None, max_length=16)
    feedback_note: str | None = Field(default=None, max_length=300)
    feedback_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
