"""Versioned, learner-specific progress report records."""

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


class StudyProgressReport(CurriculumRecord, table=True):
    __tablename__ = "st_progress_report"
    __table_args__ = (
        ForeignKeyConstraint(
            ("org_id", "learner_profile_id"),
            ("st_learner_profile.org_id", "st_learner_profile.id"),
            name="fk_st_report_learner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("org_id", "curriculum_revision_id"),
            ("st_curriculum_revision.org_id", "st_curriculum_revision.id"),
            name="fk_st_report_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("reviewer_user_id",),
            ("user.id",),
            name="fk_st_progress_report_reviewer",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("changed_by_user_id",),
            ("user.id",),
            name="fk_st_progress_report_changed_by",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(change_reason IS NULL AND changed_by_user_id IS NULL AND status <> 'withdrawn') OR "
            "(change_reason IS NOT NULL AND changed_by_user_id IS NOT NULL "
            "AND change_reason IN ('evidence_corrected','disclosure_authority','report_error','other'))",
            name="ck_st_progress_report_change",
        ),
        UniqueConstraint("uuid", "version", name="uq_st_progress_report_version"),
        UniqueConstraint(
            "org_id",
            "requester_user_id",
            "operation_uuid",
            name="uq_st_progress_report_operation",
        ),
        CheckConstraint(
            "period_from < period_to AND version >= 1 AND evidence_watermark >= 0 AND length(digest) = 64",
            name="ck_st_progress_report_snapshot",
        ),
        CheckConstraint(
            "(status = 'draft' AND reviewer_user_id IS NULL AND approved_at IS NULL) OR "
            "(status IN ('approved', 'published', 'withdrawn') AND reviewer_user_id IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_st_progress_report_review",
        ),
    )

    uuid: str = Field(default_factory=lambda: f"report_{uuid4()}", max_length=64)
    learner_profile_id: int = Field(sa_type=BigInteger)
    curriculum_revision_id: int = Field(sa_type=BigInteger)
    owner_course_uuid: str = Field(max_length=255)
    requester_user_id: int = Field(foreign_key="user.id", ondelete="RESTRICT")
    operation_uuid: str | None = Field(default=None, max_length=36)
    period_from: datetime = Field(sa_type=DateTime(timezone=True))
    period_to: datetime = Field(sa_type=DateTime(timezone=True))
    evidence_watermark: int = Field(sa_type=BigInteger)
    policy_version_ids: list[int] = Field(sa_type=JSON)
    summary: dict = Field(sa_type=JSON)
    sources: dict = Field(sa_type=JSON)
    digest: str = Field(max_length=64)
    status: str = Field(default="draft", max_length=16)
    version: int = 1
    commentary: str | None = Field(default=None, max_length=600)
    reviewer_user_id: int | None = None
    approved_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    change_reason: str | None = Field(default=None, max_length=32)
    changed_by_user_id: int | None = None


class StudyReportRecipient(CurriculumRecord, table=True):
    __tablename__ = "st_report_recipient"
    __table_args__ = (
        UniqueConstraint(
            "report_id",
            "recipient_user_id",
            "projection",
            name="uq_st_report_recipient_projection",
        ),
        CheckConstraint(
            "projection = 'adaptive_report_summary' AND grant_version >= 1",
            name="ck_st_report_recipient_scope",
        ),
    )

    report_id: int = Field(
        sa_type=BigInteger, foreign_key="st_progress_report.id", ondelete="RESTRICT"
    )
    recipient_user_id: int = Field(foreign_key="user.id", ondelete="RESTRICT")
    sharing_grant_id: int = Field(
        sa_type=BigInteger, foreign_key="st_sharing_grant.id", ondelete="RESTRICT"
    )
    grant_version: int
    projection: str = Field(max_length=64)
    available_at: datetime = Field(sa_type=DateTime(timezone=True))


class StudyReportReviewRequest(CurriculumRecord, table=True):
    __tablename__ = "st_report_review_request"
    __table_args__ = (
        UniqueConstraint("uuid", name="uq_st_report_review_request_uuid"),
        UniqueConstraint(
            "report_id",
            "requester_user_id",
            "reason",
            name="uq_st_report_review_request_reason",
        ),
        CheckConstraint(
            "reason IN ('evidence_error','report_inaccurate','privacy_concern','other')",
            name="ck_st_report_review_request_reason",
        ),
    )

    uuid: str = Field(
        default_factory=lambda: f"review_request_{uuid4()}", max_length=64
    )
    report_id: int = Field(
        sa_type=BigInteger, foreign_key="st_progress_report.id", ondelete="RESTRICT"
    )
    requester_user_id: int = Field(foreign_key="user.id", ondelete="RESTRICT")
    reason: str = Field(max_length=32)
