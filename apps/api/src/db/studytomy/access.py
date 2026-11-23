"""Independent learner access records pinned to one reviewed course release."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from src.db.studytomy.governance import _utcnow


class StudyCourseAccessGrant(SQLModel, table=True):
    __tablename__ = "st_course_access_grant"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "learner_profile_id"], ["st_learner_profile.org_id", "st_learner_profile.id"],
            ondelete="RESTRICT", name="fk_st_course_grant_learner",
        ),
        ForeignKeyConstraint(
            ["org_id", "course_uuid", "release_id"],
            ["st_course_release.org_id", "st_course_release.course_uuid", "st_course_release.id"],
            ondelete="RESTRICT", name="fk_st_course_grant_release",
        ),
        UniqueConstraint(
            "org_id", "learner_profile_id", "course_uuid", "source_kind", "source_uuid",
            name="uq_st_course_grant_source",
        ),
        CheckConstraint(
            "source_kind IN ('order_line', 'membership_cycle', 'free', 'sponsor', 'provisional')",
            name="ck_st_course_grant_source_kind",
        ),
        CheckConstraint(
            "length(source_uuid) BETWEEN 1 AND 100", name="ck_st_course_grant_source_uuid",
        ),
        CheckConstraint(
            "(start_basis IN ('after_purchase', 'fixed_period') AND starts_at IS NOT NULL "
            "AND ends_at IS NOT NULL AND duration_days IS NULL) OR "
            "(start_basis = 'after_first_access' AND duration_days BETWEEN 1 AND 36500 "
            "AND duration_days IS NOT NULL AND "
            "((starts_at IS NULL AND ends_at IS NULL) OR (starts_at IS NOT NULL AND ends_at IS NOT NULL)))",
            name="ck_st_course_grant_clock",
        ),
        CheckConstraint("ends_at > starts_at", name="ck_st_course_grant_window"),
        CheckConstraint(
            "source_kind <> 'provisional' OR start_basis = 'fixed_period'",
            name="ck_st_course_grant_provisional_window",
        ),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger().with_variant(Integer, "sqlite"))
    org_id: int
    learner_profile_id: int = Field(sa_type=BigInteger)
    course_uuid: str = Field(max_length=255)
    release_id: int = Field(sa_type=BigInteger)
    source_kind: str = Field(max_length=24)
    source_uuid: str = Field(max_length=100)
    start_basis: str = Field(max_length=24)
    duration_days: int | None = None
    starts_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    ends_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    revoked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    created_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=True))
