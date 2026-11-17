"""Privacy status and immutable, purpose-specific consent evidence."""

import re
from datetime import datetime
from enum import Enum
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic import Field as PydanticField
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from src.db.studytomy.governance import _utcnow


class StudyConsentPurpose(str, Enum):
    """Independent purposes defined by the family-learning specification."""

    LEARNING_TERMS = "learning_terms"
    AI_ASSISTANCE = "ai_assistance"
    COMMUNITY = "community"
    SHARING = "sharing"
    MARKETING = "marketing"
    PUBLICITY = "publicity"


class StudyConsentChoice(str, Enum):
    ACCEPT = "accept"
    DECLINE = "decline"
    WITHDRAW = "withdraw"


class StudyConsentEvent(SQLModel, table=True):
    """Append-only choice tied to one learner, actor, purpose and exact notice."""

    __tablename__ = "st_consent_event"
    __table_args__ = (
        UniqueConstraint("event_uuid", name="uq_st_consent_event_uuid"),
        Index(
            "ix_st_consent_event_latest",
            "org_id",
            "learner_profile_id",
            "purpose_code",
            "created_at",
            "id",
        ),
        CheckConstraint(
            "purpose_code IN ('learning_terms', 'ai_assistance', 'community', 'sharing', 'marketing', 'publicity')",
            name="ck_st_consent_event_purpose",
        ),
        CheckConstraint(
            "choice IN ('accept', 'decline', 'withdraw')",
            name="ck_st_consent_event_choice",
        ),
        CheckConstraint(
            "actor_user_id = subject_user_id",
            name="ck_st_consent_event_self_actor",
        ),
        CheckConstraint(
            "(choice = 'withdraw' AND withdraws_event_uuid IS NOT NULL) OR "
            "(choice <> 'withdraw' AND withdraws_event_uuid IS NULL)",
            name="ck_st_consent_event_withdrawal_reference",
        ),
        CheckConstraint("length(notice_version) BETWEEN 1 AND 128", name="ck_st_consent_event_notice"),
        CheckConstraint("json_array_length(scope_codes) > 0", name="ck_st_consent_event_scopes"),
        CheckConstraint(
            "json_array_length(policy_version_ids) > 0",
            name="ck_st_consent_event_policies",
        ),
        CheckConstraint(
            "provenance = 'authenticated_self_service'",
            name="ck_st_consent_event_provenance",
        ),
    )

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True),
    )
    event_uuid: str = Field(
        default_factory=lambda: f"consent_{uuid4()}",
        sa_column=Column(String(100), nullable=False),
    )
    org_id: int = Field(
        sa_column=Column(Integer, ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False)
    )
    learner_profile_id: int = Field(
        sa_column=Column(
            BigInteger().with_variant(Integer, "sqlite"),
            ForeignKey("st_learner_profile.id", ondelete="RESTRICT"),
            nullable=False,
        )
    )
    subject_user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    actor_user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    purpose_code: str = Field(sa_column=Column(String(32), nullable=False))
    choice: str = Field(sa_column=Column(String(16), nullable=False))
    withdraws_event_uuid: Optional[str] = Field(
        default=None,
        sa_column=Column(
            String(100),
            ForeignKey("st_consent_event.event_uuid", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    notice_version: str = Field(sa_column=Column(String(128), nullable=False))
    scope_codes: list[str] = Field(sa_column=Column(JSON, nullable=False))
    policy_version_ids: list[int] = Field(sa_column=Column(JSON, nullable=False))
    provenance: str = Field(
        default="authenticated_self_service",
        sa_column=Column(String(32), nullable=False, default="authenticated_self_service"),
    )
    effective_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class StudyConsentEventCreate(BaseModel):
    """An explicit choice; actor and subject are always derived server-side."""

    model_config = ConfigDict(extra="forbid")

    purpose: StudyConsentPurpose
    choice: StudyConsentChoice
    notice_version: str = PydanticField(min_length=1, max_length=128)
    scope_codes: list[str] = PydanticField(min_length=1, max_length=64)

    @field_validator("choice")
    @classmethod
    def only_new_choices(cls, value: StudyConsentChoice) -> StudyConsentChoice:
        if value == StudyConsentChoice.WITHDRAW:
            raise ValueError("Use the withdrawal action to append a withdrawal event")
        return value

    @field_validator("notice_version")
    @classmethod
    def notice_version_is_opaque(cls, value: str) -> str:
        normalized = value.strip()
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", normalized) is None:
            raise ValueError("notice_version must be a bounded opaque version reference")
        return normalized

    @field_validator("scope_codes")
    @classmethod
    def scope_codes_are_unique_opaque_codes(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if (
            len(set(normalized)) != len(normalized)
            or any(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) is None for value in normalized)
        ):
            raise ValueError("scope_codes must contain unique bounded lower-case codes")
        return normalized


class StudyConsentEventRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_uuid: str
    purpose: StudyConsentPurpose
    choice: StudyConsentChoice
    withdraws_event_uuid: Optional[str]
    notice_version: str
    scope_codes: list[str]
    policy_version_ids: list[int]
    provenance: str
    effective_at: datetime
    created_at: datetime


class StudyConsentNoticeRead(BaseModel):
    """Current approved consent copy for the authenticated learner to review."""

    model_config = ConfigDict(extra="forbid")

    purpose: StudyConsentPurpose
    notice_version: str
    notice_text: str
    scope_codes: list[str]
    policy_version_ids: list[int]


class StudyPrivacyStatusRead(SQLModel):
    learner_profile_protected: bool
    visibility_mode: Literal["private_default", "standard_account_profile"]
    publicity_controls_available: bool = False
