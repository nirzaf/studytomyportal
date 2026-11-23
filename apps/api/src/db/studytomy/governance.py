"""Persistent governance and authorization records for Studytomy workflows.

Approved policy values and named human authority are explicit database records.
Roles remain useful for broad action capabilities, but a role never substitutes
for an active owner assignment or a resource-scoped staff grant.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    JsonValue,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic import (
    Field as PydanticField,
)
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StudyPolicyKey(str, Enum):
    D_01 = "D-01"
    D_02 = "D-02"
    D_03 = "D-03"
    D_04 = "D-04"
    D_05 = "D-05"
    D_06 = "D-06"
    D_07 = "D-07"
    D_08 = "D-08"
    D_09 = "D-09"
    D_10 = "D-10"
    D_11 = "D-11"
    D_12 = "D-12"
    D_13 = "D-13"
    D_14 = "D-14"
    D_15 = "D-15"
    D_16 = "D-16"
    D_17 = "D-17"
    D_18 = "D-18"
    D_19 = "D-19"
    D_20 = "D-20"


POLICY_REQUIRED_DECISION_AREAS: dict[StudyPolicyKey, frozenset[str]] = {
    StudyPolicyKey.D_01: frozenset(
        {"markets", "learner_age_limits", "independent_participation", "independent_purchasing"}
    ),
    StudyPolicyKey.D_02: frozenset(
        {"subjects", "learner_levels", "languages", "curriculum_claims"}
    ),
    StudyPolicyKey.D_03: frozenset(
        {"guardian_authority", "multiple_guardians", "disputes", "adult_transition"}
    ),
    StudyPolicyKey.D_04: frozenset(
        {"support_routes", "monitored_hours", "response_targets", "staffing"}
    ),
    StudyPolicyKey.D_05: frozenset(
        {"prices", "currencies", "charges", "access_periods", "learner_allocation"}
    ),
    StudyPolicyKey.D_06: frozenset(
        {"membership_period", "renewal", "payment_retries", "grace_period", "recovered_payment_period"}
    ),
    StudyPolicyKey.D_07: frozenset(
        {"cancellation", "refunds", "disputes", "service_interruption", "withdrawal_remedies"}
    ),
    StudyPolicyKey.D_08: frozenset(
        {"pass_marks", "marking_method", "assistance", "attempts", "deadlines", "appeals"}
    ),
    StudyPolicyKey.D_09: frozenset(
        {"release_schedule", "completion_prerequisites", "mastery_gate"}
    ),
    StudyPolicyKey.D_10: frozenset(
        {"learner_eligibility", "supervision", "materials", "usage_limits", "review"}
    ),
    StudyPolicyKey.D_11: frozenset(
        {"community_audiences", "adult_contact_boundaries", "moderation"}
    ),
    StudyPolicyKey.D_12: frozenset(
        {"information_uses", "consent", "parent_report_scope", "publicity"}
    ),
    StudyPolicyKey.D_13: frozenset(
        {"retention", "record_access", "correction", "removal", "incident_handling"}
    ),
    StudyPolicyKey.D_14: frozenset(
        {"reminder_frequency", "quiet_periods", "contacts", "marketing_choices"}
    ),
    StudyPolicyKey.D_15: frozenset(
        {"accessibility_standard", "supported_formats", "service_recovery"}
    ),
    StudyPolicyKey.D_16: frozenset(
        {"content_rights", "review_approvals", "certificate_presentation"}
    ),
    StudyPolicyKey.D_17: frozenset(
        {"pilot_size", "pilot_duration", "comparison_method", "measures", "success_thresholds"}
    ),
    StudyPolicyKey.D_18: frozenset({"customer_terms", "selected_market_requirements"}),
    StudyPolicyKey.D_19: frozenset(
        {"staff_authority", "refund_limits", "exceptions", "financial_review_frequency"}
    ),
    StudyPolicyKey.D_20: frozenset(
        {"discounts", "bundles", "referrals", "trial_rules", "optional_additions"}
    ),
}


class StudyOwnerFunction(str, Enum):
    BUSINESS = "business"
    EDUCATION = "education"
    SAFEGUARDING = "safeguarding"
    FAMILY_SERVICES = "family_services"
    SUPPORT = "support"
    COMMERCIAL = "commercial"
    SERVICE = "service"
    PRIVACY = "privacy"


class StudyOwnerStatus(str, Enum):
    ACTIVE = "active"
    ENDED = "ended"


class StudyPolicyStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"
    WITHDRAWN = "withdrawn"


class StudyStaffScopeKind(str, Enum):
    COURSE = "course"
    LEARNER = "learner"
    CASE = "case"
    OFFER = "offer"
    ORDER = "order"
    COLLECTION = "collection"
    FUNCTION = "function"


class StudyPolicyDecisionState(str, Enum):
    PENDING = "pending"
    SPECIFIED = "specified"
    NOT_APPLICABLE = "not_applicable"


class StudyPolicyDecisionValue(BaseModel):
    """One decision area, with no implicit value or silent not-applicable state."""

    model_config = ConfigDict(extra="forbid")

    state: StudyPolicyDecisionState
    value: JsonValue | None = None
    rationale: str | None = PydanticField(default=None, max_length=2000)
    evidence_reference: str | None = PydanticField(default=None, max_length=500)

    @field_validator("rationale", "evidence_reference")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Rationale and evidence references cannot be blank")
        return normalized

    @field_validator("value")
    @classmethod
    def reject_blank_string_values(cls, value: JsonValue | None) -> JsonValue | None:
        def contains_empty_content(item: JsonValue) -> bool:
            if isinstance(item, str):
                return not item.strip()
            if isinstance(item, list):
                return not item or any(contains_empty_content(child) for child in item)
            if isinstance(item, dict):
                return not item or any(
                    not key.strip() or contains_empty_content(child)
                    for key, child in item.items()
                )
            return False

        if value is not None and contains_empty_content(value):
            raise ValueError("Specified policy values cannot contain blank strings or empty containers")
        return value

    @model_validator(mode="after")
    def state_has_consistent_evidence(self):
        has_evidence = self.rationale is not None and self.evidence_reference is not None
        if self.state == StudyPolicyDecisionState.PENDING:
            if self.value is not None:
                raise ValueError("Pending decision areas cannot contain an active value")
        elif self.state == StudyPolicyDecisionState.SPECIFIED:
            if self.value is None or not has_evidence:
                raise ValueError("Specified decision areas require a value, rationale and evidence")
        elif self.value is not None or not has_evidence:
            raise ValueError("Not-applicable decision areas require rationale and evidence only")
        return self


class StudyPolicyBody(BaseModel):
    """Typed, JSON-only decision areas; values remain explicit business inputs."""

    model_config = ConfigDict(extra="forbid")

    decision_items: dict[str, StudyPolicyDecisionValue] = PydanticField(default_factory=dict)

    def unknown_areas(self, policy_key: StudyPolicyKey) -> list[str]:
        allowed = POLICY_REQUIRED_DECISION_AREAS[policy_key]
        return sorted(set(self.decision_items) - allowed)

    def approval_blockers(self, policy_key: StudyPolicyKey) -> dict[str, list[str]]:
        required = POLICY_REQUIRED_DECISION_AREAS[policy_key]
        present = set(self.decision_items)
        return {
            "missing_decision_areas": sorted(required - present),
            "pending_decision_areas": sorted(
                area
                for area, item in self.decision_items.items()
                if item.state == StudyPolicyDecisionState.PENDING
            ),
            "unknown_decision_areas": self.unknown_areas(policy_key),
        }

    def has_approval_blockers(self, policy_key: StudyPolicyKey) -> bool:
        return any(self.approval_blockers(policy_key).values())

    def specified_value(self, area: str) -> JsonValue | None:
        item = self.decision_items.get(area)
        if item is None or item.state != StudyPolicyDecisionState.SPECIFIED:
            return None
        return item.value


def inspect_study_policy_body(
    policy_key: str | StudyPolicyKey,
    raw_body: object,
) -> tuple[StudyPolicyBody | None, dict[str, list[str]]]:
    """Parse stored JSON without trusting it; callers decide how to fail closed."""
    try:
        key = StudyPolicyKey(policy_key)
        body = StudyPolicyBody.model_validate(raw_body)
    except (TypeError, ValueError, ValidationError):
        return None, {"invalid_body": ["body"]}

    blockers = body.approval_blockers(key)
    if blockers["unknown_decision_areas"]:
        return None, blockers
    return body, blockers


class StudyOwnerAssignment(SQLModel, table=True):
    __tablename__ = "st_owner_assignment"
    __table_args__ = (
        Index("ix_st_owner_assignment_org_id", "org_id"),
        Index("ix_st_owner_assignment_owner_uuid", "owner_uuid", unique=True),
        Index(
            "ix_st_owner_assignment_lookup",
            "org_id",
            "user_id",
            "responsibility",
            "status",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    owner_uuid: str = Field(
        default_factory=lambda: str(uuid4()),
        sa_column=Column(String(100), nullable=False),
    )
    org_id: int = Field(
        sa_column=Column(Integer, ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False)
    )
    user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    responsibility: str = Field(sa_column=Column(String(32), nullable=False))
    scope_kind: str = Field(
        default="organization",
        sa_column=Column(String(32), nullable=False),
    )
    scope_uuid: str = Field(sa_column=Column(String(100), nullable=False))
    status: str = Field(
        default=StudyOwnerStatus.ACTIVE.value,
        sa_column=Column(String(16), nullable=False),
    )
    evidence_reference: str = Field(sa_column=Column(String(500), nullable=False))
    effective_from: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    effective_to: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    financial_limit_minor: Optional[int] = Field(default=None, sa_column=Column(BigInteger))
    financial_limit_currency: Optional[str] = Field(default=None, sa_column=Column(String(3)))
    delegation_evidence_reference: Optional[str] = Field(
        default=None, sa_column=Column(String(500))
    )
    delegation_policy_version_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("st_policy_version.id", ondelete="RESTRICT")
        ),
    )
    assigned_by_user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    ended_by_user_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT")),
    )
    ended_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    end_evidence_reference: Optional[str] = Field(default=None, sa_column=Column(String(500)))
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class StudyPolicyVersion(SQLModel, table=True):
    __tablename__ = "st_policy_version"
    __table_args__ = (
        Index("ix_st_policy_version_org_id", "org_id"),
        Index("ix_st_policy_version_policy_uuid", "policy_uuid", unique=True),
        Index(
            "ix_st_policy_version_scope_status",
            "org_id",
            "policy_key",
            "scope_uuid",
            "status",
        ),
        UniqueConstraint(
            "org_id",
            "policy_key",
            "scope_kind",
            "scope_uuid",
            "version",
            name="uq_st_policy_version_sequence",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    policy_uuid: str = Field(
        default_factory=lambda: str(uuid4()),
        sa_column=Column(String(100), nullable=False),
    )
    org_id: int = Field(
        sa_column=Column(Integer, ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False)
    )
    policy_key: str = Field(sa_column=Column(String(5), nullable=False))
    scope_kind: str = Field(
        default="organization",
        sa_column=Column(String(32), nullable=False),
    )
    scope_uuid: str = Field(sa_column=Column(String(100), nullable=False))
    version: int = Field(default=1, sa_column=Column(Integer, nullable=False))
    status: str = Field(
        default=StudyPolicyStatus.DRAFT.value,
        sa_column=Column(String(20), nullable=False),
    )
    body: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    evidence_reference: str = Field(sa_column=Column(String(500), nullable=False))
    evidence_sha256: Optional[str] = Field(default=None, sa_column=Column(String(64)))
    effective_from: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    effective_to: Optional[datetime] = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    created_by_user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    approved_by_user_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT")),
    )
    approved_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    supersedes_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("st_policy_version.id", ondelete="RESTRICT")
        ),
    )
    superseded_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    supersession_evidence_reference: Optional[str] = Field(
        default=None, sa_column=Column(String(500))
    )
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class StudyPolicyApproval(SQLModel, table=True):
    __tablename__ = "st_policy_approval"
    __table_args__ = (
        Index("ix_st_policy_approval_org_id", "org_id"),
        Index("ix_st_policy_approval_approval_uuid", "approval_uuid", unique=True),
        Index("ix_st_policy_approval_org_policy", "org_id", "policy_version_id"),
        UniqueConstraint(
            "policy_version_id",
            "responsibility",
            name="uq_st_policy_approval_responsibility",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    approval_uuid: str = Field(
        default_factory=lambda: str(uuid4()),
        sa_column=Column(String(100), nullable=False),
    )
    org_id: int = Field(
        sa_column=Column(Integer, ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False)
    )
    policy_version_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("st_policy_version.id", ondelete="RESTRICT"), nullable=False
        )
    )
    owner_assignment_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("st_owner_assignment.id", ondelete="RESTRICT"), nullable=False
        )
    )
    responsibility: str = Field(sa_column=Column(String(32), nullable=False))
    approver_user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    evidence_reference: str = Field(sa_column=Column(String(500), nullable=False))
    rationale: str = Field(sa_column=Column(Text, nullable=False))
    approved_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))


class StudyStaffScope(SQLModel, table=True):
    __tablename__ = "st_staff_scope"
    __table_args__ = (
        Index("ix_st_staff_scope_org_capability", "org_id", "capability"),
        Index("ix_st_staff_scope_record_uuid", "scope_record_uuid", unique=True),
        Index("ix_st_staff_scope_assignment_status", "assignment_id", "status"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    scope_record_uuid: str = Field(
        default_factory=lambda: str(uuid4()),
        sa_column=Column(String(100), nullable=False),
    )
    org_id: int = Field(
        sa_column=Column(Integer, ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False)
    )
    assignment_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("st_owner_assignment.id", ondelete="RESTRICT"), nullable=False
        )
    )
    capability: str = Field(sa_column=Column(String(64), nullable=False))
    scope_kind: str = Field(sa_column=Column(String(32), nullable=False))
    scope_uuid: Optional[str] = Field(default=None, sa_column=Column(String(100)))
    is_exclusion: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, default=False),
    )
    status: str = Field(
        default="active",
        sa_column=Column(String(16), nullable=False, default="active"),
    )
    evidence_reference: str = Field(sa_column=Column(String(500), nullable=False))
    created_by_user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    revoked_by_user_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT")),
    )
    revoked_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    revocation_evidence_reference: Optional[str] = Field(
        default=None, sa_column=Column(String(500))
    )
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class StudyBusinessEvent(SQLModel, table=True):
    __tablename__ = "st_business_event"
    __table_args__ = (
        Index("ix_st_business_event_org_resource", "org_id", "resource_uuid", "id"),
        Index("ix_st_business_event_org_subject", "org_id", "subject_uuid", "id"),
        Index("ix_st_business_event_org_created", "org_id", "created_at"),
        Index("ix_st_business_event_correlation", "correlation_id"),
        Index("ix_st_business_event_event_uuid", "event_uuid", unique=True),
        UniqueConstraint("org_id", "event_uuid", name="uq_st_business_event_org_event_uuid"),
        UniqueConstraint("dedupe_key", name="uq_st_business_event_dedupe"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    event_uuid: str = Field(
        default_factory=lambda: str(uuid4()),
        sa_column=Column(String(100), nullable=False),
    )
    org_id: int = Field(
        sa_column=Column(Integer, ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False)
    )
    actor_user_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT")),
    )
    actor_machine_id: Optional[str] = Field(default=None, sa_column=Column(String(64)))
    actor_capacity: Optional[str] = Field(default=None, sa_column=Column(String(64)))
    subject_uuid: Optional[str] = Field(default=None, sa_column=Column(String(100)))
    resource_kind: Optional[str] = Field(default=None, sa_column=Column(String(32)))
    resource_uuid: Optional[str] = Field(default=None, sa_column=Column(String(100)))
    action: str = Field(sa_column=Column(String(64), nullable=False))
    before_state: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    after_state: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    reason: Optional[str] = Field(default=None, sa_column=Column(String(2000)))
    policy_refs: Optional[list[int]] = Field(default=None, sa_column=Column(JSON))
    correlation_id: Optional[str] = Field(default=None, sa_column=Column(String(64)))
    operation_uuid: Optional[str] = Field(default=None, sa_column=Column(String(64)))
    dedupe_key: Optional[str] = Field(default=None, sa_column=Column(String(320)))
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class StudyOwnerAssignmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_uuid: str = PydanticField(min_length=1, max_length=100)
    responsibility: StudyOwnerFunction
    evidence_reference: str = PydanticField(min_length=1, max_length=500)
    effective_from: datetime
    effective_to: Optional[datetime] = None

    @field_validator("evidence_reference")
    @classmethod
    def evidence_is_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Evidence reference cannot be blank")
        return value.strip()


class StudyStaffScopeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str = PydanticField(
        pattern=(
            "^(family_authority_review|learner_dossier_read|safeguarding_case_review"
            "|learner_eligibility_review"
            "|financial_reconciliation|refund_approval|offer_publication"
            "|adaptive_curriculum_author|content_release_review|assessment_moderation|progress_report_review"
            "|privacy_retention_review|support_operating_report)$"
        )
    )
    scope_kind: StudyStaffScopeKind
    scope_uuid: str = PydanticField(min_length=1, max_length=100)
    is_exclusion: bool = False
    evidence_reference: str = PydanticField(min_length=1, max_length=500)

    @field_validator("evidence_reference")
    @classmethod
    def evidence_is_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Evidence reference cannot be blank")
        return value.strip()


class StudyStaffScopeRead(BaseModel):
    scope_record_uuid: str
    owner_uuid: str
    capability: str
    scope_kind: StudyStaffScopeKind
    scope_uuid: Optional[str]
    is_exclusion: bool
    status: str
    evidence_reference: str
    created_by_user_uuid: str
    created_at: datetime


class StudyStaffScopeRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_reference: str = PydanticField(min_length=1, max_length=500)

    @field_validator("evidence_reference")
    @classmethod
    def evidence_is_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Revocation evidence cannot be blank")
        return value.strip()


class StudyFinancialDelegationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_uuid: str = PydanticField(min_length=1, max_length=100)
    currency: str = PydanticField(min_length=3, max_length=3)
    max_amount_minor: int = PydanticField(ge=0, strict=True)
    evidence_reference: str = PydanticField(min_length=1, max_length=500)

    @field_validator("currency")
    @classmethod
    def currency_is_iso_style_code(cls, value: str) -> str:
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isalpha():
            raise ValueError("Currency must use a 3-letter code")
        return normalized

    @field_validator("evidence_reference")
    @classmethod
    def evidence_is_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Evidence reference cannot be blank")
        return value.strip()


class StudyFinancialDelegationRead(BaseModel):
    owner_uuid: str
    currency: str
    max_amount_minor: int
    policy_version_id: int
    evidence_reference: str


class StudyOwnerAssignmentRead(BaseModel):
    owner_uuid: str
    user_uuid: str
    responsibility: StudyOwnerFunction
    scope_kind: str
    scope_uuid: str
    status: StudyOwnerStatus
    evidence_reference: str
    effective_from: datetime
    effective_to: Optional[datetime]
    assigned_by_user_uuid: str
    ended_by_user_uuid: Optional[str] = None
    ended_at: Optional[datetime] = None
    end_evidence_reference: Optional[str] = None
    created_at: datetime


class StudyPolicyProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_key: StudyPolicyKey
    body: StudyPolicyBody
    evidence_reference: str = PydanticField(min_length=1, max_length=500)
    evidence_sha256: Optional[str] = PydanticField(default=None, min_length=64, max_length=64)
    effective_from: datetime
    effective_to: Optional[datetime] = None
    supersedes_policy_uuid: Optional[str] = PydanticField(default=None, max_length=100)

    @field_validator("evidence_reference")
    @classmethod
    def evidence_is_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Evidence reference cannot be blank")
        return value.strip()

    @field_validator("evidence_sha256")
    @classmethod
    def normalize_hash(cls, value: Optional[str]) -> Optional[str]:
        return value.lower() if value else value

    @model_validator(mode="after")
    def body_areas_belong_to_decision(self):
        unknown = self.body.unknown_areas(self.policy_key)
        if unknown:
            raise ValueError("Policy body contains areas outside its decision contract")
        return self


class StudyPolicyApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    responsibility: StudyOwnerFunction
    evidence_reference: str = PydanticField(min_length=1, max_length=500)
    rationale: str = PydanticField(min_length=1, max_length=2000)

    @field_validator("evidence_reference", "rationale")
    @classmethod
    def text_is_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Approval evidence and rationale cannot be blank")
        return value.strip()


class StudyPolicySupersedeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_reference: str = PydanticField(min_length=1, max_length=500)

    @field_validator("evidence_reference")
    @classmethod
    def evidence_is_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Supersession evidence cannot be blank")
        return value.strip()


class StudyPolicyApprovalRead(BaseModel):
    responsibility: StudyOwnerFunction
    approver_user_uuid: str
    evidence_reference: str
    rationale: str
    approved_at: datetime


class StudyPolicyVersionRead(BaseModel):
    policy_uuid: str
    policy_key: StudyPolicyKey
    scope_kind: str
    scope_uuid: str
    version: int
    status: StudyPolicyStatus
    body: StudyPolicyBody
    evidence_reference: str
    evidence_sha256: Optional[str]
    effective_from: datetime
    effective_to: Optional[datetime]
    created_by_user_uuid: str
    approved_at: Optional[datetime]
    supersedes_policy_uuid: Optional[str]
    created_at: datetime
    approvals: list[StudyPolicyApprovalRead] = PydanticField(default_factory=list)
