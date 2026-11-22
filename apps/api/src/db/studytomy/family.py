"""Individual learner identity and human eligibility decision records.

These records link an existing Studytomy Portal user to a specific organization. They
do not create credentials, infer guardian authority, or grant course access.
Eligibility decisions are append-only and reference the policy versions used
by the assigned reviewer.
"""

import re
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    field_validator,
    model_validator,
)
from pydantic import (
    Field as PydanticField,
)
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
    text,
)
from sqlmodel import Field, SQLModel

from src.db.studytomy.governance import _utcnow


class StudyLearnerProfileStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class StudyLearnerControlMode(str, Enum):
    UNRESOLVED = "unresolved"
    DEPENDENT = "dependent"
    INDEPENDENT = "independent"


class StudyEligibilityKind(str, Enum):
    LEARNING = "learning"
    INDEPENDENT_PARTICIPATION = "independent_participation"
    PURCHASE = "purchase"


class StudyEligibilityOutcome(str, Enum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    REVIEW_REQUIRED = "review_required"


class StudyRelationshipType(str, Enum):
    GUARDIAN = "guardian"


class StudyRelationshipStatus(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DENIED = "denied"
    WITHDRAWN = "withdrawn"
    RESTRICTED = "restricted"
    DISPUTED = "disputed"
    REVOKED = "revoked"
    EXPIRED = "expired"


class StudyRelationshipDecisionOutcome(str, Enum):
    ACCEPT = "accept"
    DENY = "deny"


class StudyRelationshipRestrictionAction(str, Enum):
    RESTRICT = "restrict"
    DISPUTE = "dispute"
    REVOKE = "revoke"


class StudySharingGrantStatus(str, Enum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"


class StudyRelationship(SQLModel, table=True):
    """A single learner/adult authority request with a versioned staff decision.

    The row is not itself an access grant. Consumers must resolve a current
    accepted decision under the current D-03, D-12 and D-13 policies.
    """

    __tablename__ = "st_relationship"
    __table_args__ = (
        Index("ix_st_relationship_uuid", "relationship_uuid", unique=True),
        Index(
            "ix_st_relationship_current",
            "org_id",
            "learner_profile_id",
            "adult_user_id",
            "status",
            "valid_to",
        ),
        Index("ix_st_relationship_review", "org_id", "status", "created_at"),
        CheckConstraint(
            "relationship_type IN ('guardian')",
            name="ck_st_relationship_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'denied', 'withdrawn', 'restricted', 'disputed', 'revoked', 'expired')",
            name="ck_st_relationship_status",
        ),
        CheckConstraint(
            "authority_version >= 1", name="ck_st_relationship_authority_version"
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from",
            name="ck_st_relationship_validity_range",
        ),
        CheckConstraint(
            "status <> 'accepted' OR (valid_from IS NOT NULL AND valid_to IS NOT NULL AND json_array_length(approved_scopes) > 0)",
            name="ck_st_relationship_accepted_has_scope_and_period",
        ),
        CheckConstraint(
            "status = 'accepted' OR json_array_length(approved_scopes) = 0",
            name="ck_st_relationship_nonaccepted_has_no_scope",
        ),
        CheckConstraint(
            "status IN ('pending', 'withdrawn') OR (decided_by_user_id IS NOT NULL AND decision_owner_assignment_id IS NOT NULL AND decided_at IS NOT NULL AND decision_evidence_reference IS NOT NULL AND decision_reason_code IS NOT NULL AND decision_policy_version_ids IS NOT NULL)",
            name="ck_st_relationship_decision_complete",
        ),
        CheckConstraint(
            "status NOT IN ('pending', 'withdrawn') OR (decided_by_user_id IS NULL AND decision_owner_assignment_id IS NULL AND decided_at IS NULL AND decision_evidence_reference IS NULL AND decision_reason_code IS NULL AND decision_policy_version_ids IS NULL)",
            name="ck_st_relationship_pending_has_no_decision",
        ),
        CheckConstraint(
            "status <> 'withdrawn' OR (withdrawn_by_user_id IS NOT NULL AND withdrawal_evidence_reference IS NOT NULL AND withdrawal_reason_code IS NOT NULL AND withdrawn_at IS NOT NULL)",
            name="ck_st_relationship_withdrawal_complete",
        ),
        CheckConstraint(
            "status = 'withdrawn' OR (withdrawn_by_user_id IS NULL AND withdrawal_evidence_reference IS NULL AND withdrawal_reason_code IS NULL AND withdrawn_at IS NULL)",
            name="ck_st_relationship_nonwithdrawn_has_no_withdrawal",
        ),
    )

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True),
    )
    relationship_uuid: str = Field(
        default_factory=lambda: f"rel_{uuid4()}",
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
    adult_user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    requested_by_user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    relationship_type: str = Field(
        default=StudyRelationshipType.GUARDIAN.value,
        sa_column=Column(String(24), nullable=False, default=StudyRelationshipType.GUARDIAN.value),
    )
    requested_scopes: list[str] = Field(sa_column=Column(JSON, nullable=False))
    approved_scopes: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    status: str = Field(
        default=StudyRelationshipStatus.PENDING.value,
        sa_column=Column(String(16), nullable=False, default=StudyRelationshipStatus.PENDING.value),
    )
    request_evidence_reference: str = Field(sa_column=Column(String(500), nullable=False))
    request_policy_version_ids: list[int] = Field(sa_column=Column(JSON, nullable=False))
    decision_policy_version_ids: Optional[list[int]] = Field(
        default=None, sa_column=Column(JSON(none_as_null=True))
    )
    valid_from: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    valid_to: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    decided_by_user_id: Optional[int] = Field(
        default=None, sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"))
    )
    decision_owner_assignment_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("st_owner_assignment.id", ondelete="RESTRICT")),
    )
    decision_evidence_reference: Optional[str] = Field(default=None, sa_column=Column(String(500)))
    decision_reason_code: Optional[str] = Field(default=None, sa_column=Column(String(64)))
    decided_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    withdrawn_by_user_id: Optional[int] = Field(
        default=None, sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"))
    )
    withdrawal_evidence_reference: Optional[str] = Field(default=None, sa_column=Column(String(500)))
    withdrawal_reason_code: Optional[str] = Field(default=None, sa_column=Column(String(64)))
    withdrawn_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    authority_version: int = Field(default=1, sa_column=Column(Integer, nullable=False, default=1))
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class StudySharingGrant(SQLModel, table=True):
    """A learner-controlled, time-bounded sharing choice separate from guardianship."""

    __tablename__ = "st_sharing_grant"
    __table_args__ = (
        Index("ix_st_sharing_grant_uuid", "grant_uuid", unique=True),
        Index(
            "ix_st_sharing_grant_recipient",
            "org_id",
            "learner_profile_id",
            "recipient_user_id",
            "status",
            "valid_to",
        ),
        CheckConstraint(
            "status IN ('active', 'withdrawn')", name="ck_st_sharing_grant_status"
        ),
        CheckConstraint("authority_version >= 1", name="ck_st_sharing_grant_version"),
        CheckConstraint("valid_to > valid_from", name="ck_st_sharing_grant_period"),
        CheckConstraint(
            "json_array_length(scope_codes) > 0", name="ck_st_sharing_grant_scopes"
        ),
        CheckConstraint(
            "status <> 'withdrawn' OR withdrawn_at IS NOT NULL",
            name="ck_st_sharing_grant_withdrawal_complete",
        ),
        CheckConstraint(
            "status <> 'active' OR withdrawn_at IS NULL",
            name="ck_st_sharing_grant_active_has_no_withdrawal",
        ),
    )

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True),
    )
    grant_uuid: str = Field(
        default_factory=lambda: f"share_{uuid4()}",
        sa_column=Column(String(100), nullable=False),
    )
    org_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
        )
    )
    learner_profile_id: int = Field(
        sa_column=Column(
            BigInteger().with_variant(Integer, "sqlite"),
            ForeignKey("st_learner_profile.id", ondelete="RESTRICT"),
            nullable=False,
        )
    )
    recipient_user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    created_by_user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    scope_codes: list[str] = Field(sa_column=Column(JSON, nullable=False))
    policy_version_ids: list[int] = Field(sa_column=Column(JSON, nullable=False))
    status: str = Field(
        default=StudySharingGrantStatus.ACTIVE.value,
        sa_column=Column(
            String(16), nullable=False, default=StudySharingGrantStatus.ACTIVE.value
        ),
    )
    valid_from: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    valid_to: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    authority_version: int = Field(
        default=1, sa_column=Column(Integer, nullable=False, default=1)
    )
    withdrawn_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class StudyLearnerProfile(SQLModel, table=True):
    """A learner-specific link to one existing organization member."""

    __tablename__ = "st_learner_profile"
    __table_args__ = (
        Index("ix_st_learner_profile_uuid", "learner_uuid", unique=True),
        Index("ix_st_learner_profile_org_status", "org_id", "status"),
        Index("ix_st_learner_profile_user_id", "user_id"),
        UniqueConstraint("org_id", "user_id", name="uq_st_learner_profile_org_user"),
        UniqueConstraint("org_id", "id", name="uq_st_learner_profile_org_id"),
        CheckConstraint("access_version >= 1", name="ck_st_learner_profile_access_version"),
        CheckConstraint(
            "control_mode IN ('unresolved', 'dependent', 'independent')",
            name="ck_st_learner_profile_control_mode",
        ),
        CheckConstraint(
            "status IN ('pending', 'active', 'suspended', 'closed')",
            name="ck_st_learner_profile_status",
        ),
    )

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True),
    )
    learner_uuid: str = Field(
        default_factory=lambda: str(uuid4()),
        sa_column=Column(String(100), nullable=False),
    )
    org_id: int = Field(
        sa_column=Column(Integer, ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False)
    )
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    status: str = Field(
        default=StudyLearnerProfileStatus.PENDING.value,
        sa_column=Column(String(16), nullable=False, default=StudyLearnerProfileStatus.PENDING.value),
    )
    control_mode: str = Field(
        default=StudyLearnerControlMode.UNRESOLVED.value,
        sa_column=Column(String(16), nullable=False, default=StudyLearnerControlMode.UNRESOLVED.value),
    )
    time_zone: Optional[str] = Field(default=None, sa_column=Column(String(64)))
    access_version: int = Field(default=1, sa_column=Column(Integer, nullable=False, default=1))
    created_by_user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class StudyEligibilityDecision(SQLModel, table=True):
    """Immutable human decision; a new row supersedes the prior decision."""

    __tablename__ = "st_eligibility_decision"
    __table_args__ = (
        Index("ix_st_eligibility_decision_uuid", "decision_uuid", unique=True),
        Index(
            "ix_st_eligibility_decision_profile_kind",
            "org_id",
            "learner_profile_id",
            "eligibility_kind",
            "created_at",
        ),
        Index(
            "uq_st_eligibility_decision_root",
            "org_id",
            "learner_profile_id",
            "eligibility_kind",
            unique=True,
            postgresql_where=text("supersedes_id IS NULL"),
            sqlite_where=text("supersedes_id IS NULL"),
        ),
        UniqueConstraint("supersedes_id", name="uq_st_eligibility_decision_successor"),
        CheckConstraint(
            "eligibility_kind IN ('learning', 'independent_participation', 'purchase')",
            name="ck_st_eligibility_decision_kind",
        ),
        CheckConstraint(
            "outcome IN ('eligible', 'ineligible', 'review_required')",
            name="ck_st_eligibility_decision_outcome",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_st_eligibility_decision_effective_range",
        ),
        CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id", name="ck_st_eligibility_decision_not_self_superseding"),
    )

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True),
    )
    decision_uuid: str = Field(
        default_factory=lambda: str(uuid4()),
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
    eligibility_kind: str = Field(sa_column=Column(String(32), nullable=False))
    outcome: str = Field(sa_column=Column(String(24), nullable=False))
    policy_version_ids: list[int] = Field(sa_column=Column(JSON, nullable=False))
    evidence_reference: str = Field(sa_column=Column(String(500), nullable=False))
    effective_from: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    effective_to: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    review_due_at: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    reviewer_user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    owner_assignment_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("st_owner_assignment.id", ondelete="RESTRICT"),
            nullable=False,
        )
    )
    supersedes_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger().with_variant(Integer, "sqlite"),
            ForeignKey("st_eligibility_decision.id", ondelete="RESTRICT"),
        ),
    )
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class StudyLearnerProfileCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_uuid: str = PydanticField(min_length=1, max_length=100)
    time_zone: Optional[str] = PydanticField(default=None, min_length=1, max_length=64)

    @field_validator("time_zone")
    @classmethod
    def time_zone_is_iana(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("time_zone cannot be blank")
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as error:
            raise ValueError("time_zone must name an installed IANA time zone") from error
        return normalized


class StudyEligibilityDecisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    eligibility_kind: StudyEligibilityKind
    outcome: StudyEligibilityOutcome
    evidence_reference: str = PydanticField(min_length=1, max_length=500)
    effective_to: Optional[datetime] = None
    review_due_at: Optional[datetime] = None
    control_mode: Optional[StudyLearnerControlMode] = None

    @field_validator("evidence_reference")
    @classmethod
    def evidence_reference_is_opaque(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not normalized
            or len(normalized) > 500
            or any(not (char.isascii() and (char.isalnum() or char in "._/-")) for char in normalized)
        ):
            raise ValueError("evidence_reference must be a bounded opaque reference")
        return normalized

    @field_validator("effective_to", "review_due_at")
    @classmethod
    def decision_dates_require_time_zone(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and value.tzinfo is None:
            raise ValueError("Decision timestamps must include a timezone")
        return value


class StudyLearnerActivationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_access_version: int = PydanticField(strict=True, ge=1)
    reason_code: str = PydanticField(min_length=1, max_length=64)

    @field_validator("reason_code")
    @classmethod
    def reason_code_is_safe(cls, value: str) -> str:
        normalized = value.strip()
        if (
            not normalized
            or len(normalized) > 64
            or any(not (char.isascii() and (char.isalnum() or char in "_-")) for char in normalized)
        ):
            raise ValueError("reason_code must be a bounded opaque code")
        return normalized


class StudyEligibilityDecisionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_uuid: str
    eligibility_kind: StudyEligibilityKind
    outcome: StudyEligibilityOutcome
    policy_version_ids: list[int]
    evidence_reference: str
    effective_from: datetime
    effective_to: Optional[datetime]
    review_due_at: Optional[datetime]
    policy_current: bool
    currently_eligible: bool
    created_at: datetime


class StudyLearnerProfileRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    learner_uuid: str
    user_uuid: str
    status: StudyLearnerProfileStatus
    control_mode: StudyLearnerControlMode
    time_zone: Optional[str]
    access_version: int
    eligibility_decisions: list[StudyEligibilityDecisionRead]
    created_at: datetime
    updated_at: datetime


class StudyRelationshipRequestCreate(BaseModel):
    """A learner/adult scope request; the authenticated adult is the subject."""

    model_config = ConfigDict(extra="forbid")

    requested_scopes: list[str] = PydanticField(min_length=1, max_length=16)
    evidence_reference: str = PydanticField(min_length=20, max_length=100)

    @field_validator("requested_scopes")
    @classmethod
    def scopes_are_bounded_codes(cls, value: list[str]) -> list[str]:
        normalized = [scope.strip() for scope in value]
        if (
            len(set(normalized)) != len(normalized)
            or any(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", scope) is None for scope in normalized)
        ):
            raise ValueError("requested_scopes must contain unique bounded scope codes")
        return normalized

    @field_validator("evidence_reference")
    @classmethod
    def evidence_reference_is_opaque(cls, value: str) -> str:
        normalized = value.strip()
        if re.fullmatch(r"(?:ev|case)_[A-Za-z0-9_-]{16,80}", normalized) is None:
            raise ValueError("evidence_reference must be an opaque evidence or case reference")
        return normalized


class StudyRelationshipDecisionCreate(BaseModel):
    """Versioned staff decision; an accepted scope is always explicitly bounded."""

    model_config = ConfigDict(extra="forbid")

    outcome: StudyRelationshipDecisionOutcome
    expected_version: int = PydanticField(ge=1)
    approved_scopes: list[str] = PydanticField(default_factory=list, max_length=16)
    valid_from: Optional[datetime] = None
    valid_to: Optional[datetime] = None
    evidence_reference: str = PydanticField(min_length=20, max_length=100)
    reason_code: str = PydanticField(min_length=3, max_length=64)

    @field_validator("approved_scopes")
    @classmethod
    def approved_scopes_are_bounded_codes(cls, value: list[str]) -> list[str]:
        normalized = [scope.strip() for scope in value]
        if (
            len(set(normalized)) != len(normalized)
            or any(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", scope) is None for scope in normalized)
        ):
            raise ValueError("approved_scopes must contain unique bounded scope codes")
        return normalized

    @field_validator("evidence_reference")
    @classmethod
    def decision_evidence_reference_is_opaque(cls, value: str) -> str:
        normalized = value.strip()
        if re.fullmatch(r"(?:ev|case)_[A-Za-z0-9_-]{16,80}", normalized) is None:
            raise ValueError("evidence_reference must be an opaque evidence or case reference")
        return normalized

    @field_validator("reason_code")
    @classmethod
    def decision_reason_is_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", normalized) is None:
            raise ValueError("reason_code must be a stable non-PII code")
        return normalized

    @field_validator("valid_from", "valid_to")
    @classmethod
    def decision_dates_require_time_zone(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and value.tzinfo is None:
            raise ValueError("Relationship timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def outcome_has_consistent_scope_and_period(self):
        if self.outcome == StudyRelationshipDecisionOutcome.ACCEPT:
            if not self.approved_scopes or self.valid_to is None:
                raise ValueError("Accepted authority requires explicit scopes and a validity end time")
            if self.valid_from is not None and self.valid_to <= self.valid_from:
                raise ValueError("valid_to must be later than valid_from")
        elif self.approved_scopes or self.valid_from is not None or self.valid_to is not None:
            raise ValueError("Denied authority cannot contain scopes or a validity period")
        return self


class StudyRelationshipWithdrawalCreate(BaseModel):
    """Requester-controlled, versioned closure of a pending authority request."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = PydanticField(ge=1)
    evidence_reference: str = PydanticField(min_length=20, max_length=100)
    reason_code: str = PydanticField(min_length=3, max_length=64)

    @field_validator("evidence_reference")
    @classmethod
    def withdrawal_evidence_reference_is_opaque(cls, value: str) -> str:
        normalized = value.strip()
        if re.fullmatch(r"(?:ev|case)_[A-Za-z0-9_-]{16,80}", normalized) is None:
            raise ValueError("evidence_reference must be an opaque evidence or case reference")
        return normalized

    @field_validator("reason_code")
    @classmethod
    def withdrawal_reason_is_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", normalized) is None:
            raise ValueError("reason_code must be a stable non-PII code")
        return normalized


class StudyRelationshipRestrictionCreate(BaseModel):
    """Independent, versioned narrowing/revocation of an existing authority."""

    model_config = ConfigDict(extra="forbid")

    action: StudyRelationshipRestrictionAction
    expected_version: int = PydanticField(ge=1)
    evidence_reference: str = PydanticField(min_length=20, max_length=100)
    reason_code: str = PydanticField(min_length=3, max_length=64)

    @field_validator("evidence_reference")
    @classmethod
    def restriction_evidence_reference_is_opaque(cls, value: str) -> str:
        normalized = value.strip()
        if re.fullmatch(r"(?:ev|case)_[A-Za-z0-9_-]{16,80}", normalized) is None:
            raise ValueError("evidence_reference must be an opaque evidence or case reference")
        return normalized

    @field_validator("reason_code")
    @classmethod
    def restriction_reason_is_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if re.fullmatch(r"[A-Z][A-Z0-9_]{2,63}", normalized) is None:
            raise ValueError("reason_code must be a stable non-PII code")
        return normalized


class StudySharingGrantCreate(BaseModel):
    """A learner's explicit voluntary disclosure choice."""

    model_config = ConfigDict(extra="forbid")

    recipient_user_uuid: str = PydanticField(min_length=1, max_length=100)
    scope_codes: list[str] = PydanticField(min_length=1, max_length=16)
    valid_to: datetime

    @field_validator("scope_codes")
    @classmethod
    def scopes_are_bounded_codes(cls, value: list[str]) -> list[str]:
        normalized = [scope.strip() for scope in value]
        if (
            len(set(normalized)) != len(normalized)
            or any(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", scope) is None for scope in normalized)
        ):
            raise ValueError("scope_codes must contain unique bounded scope codes")
        return normalized

    @field_validator("recipient_user_uuid")
    @classmethod
    def recipient_reference_is_opaque(cls, value: str) -> str:
        normalized = value.strip()
        if re.fullmatch(r"user_[A-Za-z0-9_-]{1,96}", normalized) is None:
            raise ValueError("recipient_user_uuid must be an opaque user reference")
        return normalized

    @field_validator("valid_to")
    @classmethod
    def sharing_end_requires_time_zone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Sharing timestamps must include a timezone")
        return value


class StudySharingGrantRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_uuid: str
    recipient_user_uuid: str
    scope_codes: list[str]
    policy_version_ids: list[int]
    status: StudySharingGrantStatus
    valid_from: datetime
    valid_to: datetime
    authority_version: int
    withdrawn_at: Optional[datetime]
    created_at: datetime


class StudySharingGrantWithdrawal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = PydanticField(ge=1)


class StudyRelationshipRead(BaseModel):
    """Safe projection for the adult who submitted the request."""

    model_config = ConfigDict(extra="forbid")

    relationship_uuid: str
    learner_uuid: str
    relationship_type: StudyRelationshipType
    status: StudyRelationshipStatus
    requested_scopes: list[str]
    approved_scopes: list[str]
    valid_from: Optional[datetime]
    valid_to: Optional[datetime]
    authority_version: int
    created_at: datetime
    updated_at: datetime
    decided_at: Optional[datetime]


class StudyRelationshipReviewRead(StudyRelationshipRead):
    """Additional evidence and adult identity fields for an assigned reviewer."""

    adult_user_uuid: str
    request_evidence_reference: str
    request_policy_version_ids: list[int]
    decision_policy_version_ids: Optional[list[int]]
    decision_evidence_reference: Optional[str]
    decision_reason_code: Optional[str]
    withdrawal_evidence_reference: Optional[str]
    withdrawal_reason_code: Optional[str]
    withdrawn_at: Optional[datetime]


class StudyFamilyReviewQueueItem(BaseModel):
    """Minimal staff queue row; evidence remains behind the assigned-review route."""

    model_config = ConfigDict(extra="forbid")

    learner_uuid: str
    relationship_uuid: str
    status: StudyRelationshipStatus
    requested_scopes: list[str]
    approved_scopes: list[str]
    authority_version: int
    created_at: datetime
    updated_at: datetime
