"""Purpose-scoped service case metadata. Confidential attachments remain in private evidence storage."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKeyConstraint, Index, Text, UniqueConstraint
from sqlmodel import Field, SQLModel


class StudyServiceCase(SQLModel, table=True):
    __tablename__ = "st_service_case"
    __table_args__ = (
        UniqueConstraint("org_id", "case_uuid", name="uq_st_service_case_org_uuid"),
        CheckConstraint(
            "category IN ('support', 'safeguarding')",
            name="ck_st_service_case_category",
        ),
        CheckConstraint(
            "(status = 'acknowledged' AND owner_user_id IS NULL) OR "
            "(status IN ('assigned', 'awaiting_information', 'under_review', 'resolved', 'closed') "
            "AND owner_user_id IS NOT NULL)",
            name="ck_st_service_case_state_owner",
        ),
        CheckConstraint(
            "owner_user_id IS NULL OR owner_user_id <> reporter_user_id",
            name="ck_st_service_case_owner_not_reporter",
        ),
        CheckConstraint("version >= 1", name="ck_st_service_case_version"),
        CheckConstraint(
            "(reporter_user_id IS NOT NULL AND guest_contact_verified_at IS NULL) OR "
            "(reporter_user_id IS NULL AND guest_contact_verified_at IS NOT NULL "
            "AND category = 'support' AND support_topic IS NOT NULL)",
            name="ck_st_service_case_reporter_kind",
        ),
        CheckConstraint(
            "support_topic IS NULL OR (category = 'support' AND length(support_topic) BETWEEN 1 AND 100)",
            name="ck_st_service_case_support_topic",
        ),
        Index("ix_st_service_case_org_state", "org_id", "category", "status", "id"),
        Index("ix_st_service_case_reporter", "org_id", "reporter_user_id", "id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    case_uuid: str = Field(default_factory=lambda: str(uuid4()), max_length=36)
    org_id: int = Field(foreign_key="organization.id", ondelete="RESTRICT")
    reporter_user_id: int | None = Field(default=None, foreign_key="user.id", ondelete="RESTRICT")
    guest_contact_verified_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    owner_user_id: int | None = Field(default=None, foreign_key="user.id", ondelete="RESTRICT")
    category: str = Field(max_length=32)
    support_topic: str | None = Field(default=None, max_length=100)
    status: str = Field(default="acknowledged", max_length=32)
    version: int = Field(default=1)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class StudyServiceCaseExclusion(SQLModel, table=True):
    __tablename__ = "st_service_case_exclusion"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "case_uuid"],
            ["st_service_case.org_id", "st_service_case.case_uuid"],
            name="fk_st_service_case_exclusion_org_case",
            ondelete="RESTRICT",
        ),
    )

    org_id: int = Field(primary_key=True)
    case_uuid: str = Field(primary_key=True, max_length=36)
    user_id: int = Field(primary_key=True, foreign_key="user.id", ondelete="RESTRICT")


class StudyGuestSupportChallenge(SQLModel, table=True):
    """Short-lived verification state kept apart from users and case records."""

    __tablename__ = "st_guest_support_challenge"
    __table_args__ = (
        UniqueConstraint("org_id", "token_digest", name="uq_st_guest_support_challenge_org_token"),
        ForeignKeyConstraint(
            ["org_id", "case_uuid"], ["st_service_case.org_id", "st_service_case.case_uuid"],
            name="fk_st_guest_support_challenge_org_case", ondelete="RESTRICT",
        ),
        CheckConstraint("length(token_digest) = 64", name="ck_st_guest_support_challenge_digest"),
        CheckConstraint("length(contact_ciphertext) > 0", name="ck_st_guest_support_challenge_contact"),
        CheckConstraint("length(topic) BETWEEN 1 AND 100", name="ck_st_guest_support_challenge_topic"),
        CheckConstraint(
            "created_at < expires_at AND "
            "((consumed_at IS NULL AND case_uuid IS NULL) OR "
            "(consumed_at IS NOT NULL AND case_uuid IS NOT NULL "
            "AND consumed_at >= created_at AND consumed_at < expires_at))",
            name="ck_st_guest_support_challenge_window",
        ),
        Index("ix_st_guest_support_challenge_expiry", "expires_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    org_id: int = Field(foreign_key="organization.id", ondelete="CASCADE")
    token_digest: str = Field(max_length=64)
    contact_ciphertext: str = Field(sa_column=Column(Text, nullable=False, info={"private": True}))
    topic: str = Field(max_length=100)
    created_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    consumed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    case_uuid: str | None = Field(default=None, max_length=36)


class StudySafeguardingNarrative(SQLModel, table=True):
    """Confidential reporter text, queried separately from case metadata."""

    __tablename__ = "st_safeguarding_narrative"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "case_uuid"],
            ["st_service_case.org_id", "st_service_case.case_uuid"],
            name="fk_st_safeguarding_narrative_org_case", ondelete="RESTRICT",
        ),
        CheckConstraint("length(narrative) BETWEEN 1 AND 8000", name="ck_st_safeguarding_narrative_length"),
    )

    org_id: int = Field(primary_key=True)
    case_uuid: str = Field(primary_key=True, max_length=36)
    narrative: str = Field(sa_column=Column(Text, nullable=False, info={"private": True}))
    retention_policy_id: int = Field(foreign_key="st_policy_version.id", ondelete="RESTRICT")
    retention_category: str = Field(max_length=64)
