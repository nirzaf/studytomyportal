"""Exact-candidate course review decisions."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from src.db.studytomy.governance import _utcnow


class StudyCourseReview(SQLModel, table=True):
    __tablename__ = "st_course_review"
    __table_args__ = (
        UniqueConstraint("uuid", name="uq_st_course_review_uuid"),
        Index(
            "ix_st_course_review_candidate",
            "org_id",
            "course_uuid",
            "revision_sha256",
            "id",
        ),
        CheckConstraint(
            "length(revision_sha256) = 64", name="ck_st_course_review_digest"
        ),
        CheckConstraint(
            "owner_user_id <> reviewer_user_id", name="ck_st_course_review_independent"
        ),
        CheckConstraint(
            "rights_evidence_id <> accessibility_evidence_id",
            name="ck_st_course_review_evidence",
        ),
        CheckConstraint(
            "decision IN ('approved', 'changes_requested') AND "
            "(decision <> 'approved' OR (accessibility_confirmed AND assessment_confirmed))",
            name="ck_st_course_review_findings",
        ),
    )

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_type=BigInteger().with_variant(Integer, "sqlite"),
    )
    uuid: str = Field(default_factory=lambda: f"course_review_{uuid4()}", max_length=64)
    org_id: int = Field(foreign_key="organization.id", ondelete="RESTRICT")
    course_uuid: str = Field(max_length=255)
    revision_sha256: str = Field(max_length=64)
    owner_user_id: int = Field(foreign_key="user.id", ondelete="RESTRICT")
    reviewer_user_id: int = Field(foreign_key="user.id", ondelete="RESTRICT")
    reviewer_assignment_id: int = Field(
        foreign_key="st_owner_assignment.id", ondelete="RESTRICT"
    )
    rights_evidence_id: int = Field(
        sa_type=BigInteger, foreign_key="st_evidence_file.id", ondelete="RESTRICT"
    )
    accessibility_evidence_id: int = Field(
        sa_type=BigInteger, foreign_key="st_evidence_file.id", ondelete="RESTRICT"
    )
    policy_version_ids: list[int] = Field(sa_type=JSON)
    decision: str = Field(max_length=24)
    accessibility_confirmed: bool
    assessment_confirmed: bool
    media_manifest: dict | None = Field(default=None, sa_type=JSON(none_as_null=True))
    created_at: datetime = Field(
        default_factory=_utcnow, sa_type=DateTime(timezone=True)
    )


class StudyCourseRelease(SQLModel, table=True):
    """A private, retained release tied to one reviewed candidate."""

    __tablename__ = "st_course_release"
    __table_args__ = (
        UniqueConstraint("uuid", name="uq_st_course_release_uuid"),
        UniqueConstraint("review_id", name="uq_st_course_release_review"),
        UniqueConstraint("org_id", "course_uuid", "id", name="uq_st_course_release_scope"),
        Index("ix_st_course_release_course", "org_id", "course_uuid", "id"),
        CheckConstraint(
            "length(snapshot_sha256) = 64", name="ck_st_course_release_digest"
        ),
        CheckConstraint(
            "length(retention_category) BETWEEN 1 AND 64",
            name="ck_st_course_release_retention",
        ),
    )

    id: int | None = Field(
        default=None,
        primary_key=True,
        sa_type=BigInteger().with_variant(Integer, "sqlite"),
    )
    uuid: str = Field(
        default_factory=lambda: f"course_release_{uuid4()}", max_length=64
    )
    org_id: int = Field(foreign_key="organization.id", ondelete="RESTRICT")
    course_uuid: str = Field(max_length=255)
    review_id: int = Field(
        sa_type=BigInteger, foreign_key="st_course_review.id", ondelete="RESTRICT"
    )
    snapshot_sha256: str = Field(max_length=64)
    snapshot: dict = Field(sa_type=JSON)
    media_manifest: dict = Field(sa_type=JSON)
    assessment_policy_id: int = Field(
        foreign_key="st_policy_version.id", ondelete="RESTRICT"
    )
    progression_policy_id: int = Field(
        foreign_key="st_policy_version.id", ondelete="RESTRICT"
    )
    retention_policy_id: int = Field(
        foreign_key="st_policy_version.id", ondelete="RESTRICT"
    )
    rights_policy_id: int = Field(
        foreign_key="st_policy_version.id", ondelete="RESTRICT"
    )
    retention_category: str = Field(max_length=64)
    policy_binding: dict | None = Field(default=None, sa_type=JSON(none_as_null=True))
    created_at: datetime = Field(
        default_factory=_utcnow, sa_type=DateTime(timezone=True)
    )
