"""Private, versioned Studytomy offer drafts (TASK-017).

Offer versions preserve the exact proposed terms used by later commerce work.
This module deliberately has no published, purchasable, or accepted-order state:
those require the unresolved decisions listed in the family-learning spec.
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field as PydanticField,
    field_validator,
)
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from src.db.studytomy.governance import _utcnow


class StudyOfferKind(str, Enum):
    COURSE = "course"
    PROGRAMME = "programme"
    MEMBERSHIP = "membership"


class StudyOfferResourceKind(str, Enum):
    COURSE = "course"
    PROGRAMME_CONTENT = "programme_content"


class StudyOfferPriceLineKind(str, Enum):
    CHARGE = "charge"
    TAX = "tax"
    DISCOUNT = "discount"


class StudyOfferAssessmentMethod(str, Enum):
    NONE = "none"
    OBJECTIVE = "objective"
    EDUCATOR_MARKED = "educator_marked"
    MIXED = "mixed"
    OTHER = "other"


class StudyOfferAwardKind(str, Enum):
    NONE = "none"
    PARTICIPATION = "participation"
    COMPLETION = "completion"
    CERTIFICATE = "certificate"


class StudyOfferAccessClock(str, Enum):
    AFTER_PURCHASE = "after_purchase"
    AFTER_FIRST_ACCESS = "after_first_access"
    FIXED_PERIOD = "fixed_period"


class StudyOfferAccessTerms(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clock: Optional[StudyOfferAccessClock] = None
    duration_days: Optional[int] = PydanticField(default=None, ge=1, le=36500)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None


class StudyOfferResourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: StudyOfferResourceKind
    resource_uuid: str = PydanticField(min_length=1, max_length=100)
    # Overwritten by the service from the organization-scoped Course row.
    title_snapshot: Optional[str] = PydanticField(default=None, max_length=1000)
    release_uuid: Optional[str] = PydanticField(default=None, min_length=1, max_length=64)
    release_sha256: Optional[str] = PydanticField(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("resource_uuid")
    @classmethod
    def normalize_resource_uuid(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("resource_uuid cannot be blank")
        return normalized

    @field_validator("title_snapshot")
    @classmethod
    def normalize_title_snapshot(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class StudyOfferPrerequisite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = PydanticField(min_length=1, max_length=500)
    resource: Optional[StudyOfferResourceReference] = None

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("prerequisite description cannot be blank")
        return normalized


class StudyOfferPriceLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: StudyOfferPriceLineKind
    label: str = PydanticField(min_length=1, max_length=120)
    amount_minor: int = PydanticField(ge=0, le=9_000_000_000_000_000)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("price line label cannot be blank")
        return normalized


class StudyOfferPriceTerms(BaseModel):
    """Proposed minor-unit price; never treated as an approved charge."""

    model_config = ConfigDict(extra="forbid")

    currency: Optional[str] = PydanticField(default=None, min_length=3, max_length=3)
    lines: list[StudyOfferPriceLine] = PydanticField(default_factory=list, max_length=30)
    total_minor: Optional[int] = PydanticField(
        default=None, ge=0, le=9_000_000_000_000_000
    )

    @field_validator("currency")
    @classmethod
    def normalize_currency_syntax(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip().upper()
        if len(normalized) != 3 or not normalized.isascii() or not normalized.isalpha():
            raise ValueError("currency must be a three-letter ASCII code")
        return normalized

    def is_complete(self) -> bool:
        if self.currency is None or self.total_minor is None or not self.lines:
            return False
        calculated = sum(
            -line.amount_minor
            if line.kind == StudyOfferPriceLineKind.DISCOUNT
            else line.amount_minor
            for line in self.lines
        )
        return calculated >= 0 and calculated == self.total_minor


class StudyOfferAssessmentTerms(BaseModel):
    model_config = ConfigDict(extra="forbid")

    method: Optional[StudyOfferAssessmentMethod] = None
    description: Optional[str] = PydanticField(default=None, max_length=2000)


class StudyOfferAwardTerms(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Optional[StudyOfferAwardKind] = None
    description: Optional[str] = PydanticField(default=None, max_length=1000)


class StudyOfferTerms(BaseModel):
    """A full proposed-term document. Missing decisions remain draft blockers."""

    model_config = ConfigDict(extra="forbid")

    title: Optional[str] = PydanticField(default=None, max_length=200)
    summary: Optional[str] = PydanticField(default=None, max_length=4000)
    goals: Optional[list[str]] = PydanticField(default=None, max_length=32)
    audience: Optional[str] = PydanticField(default=None, max_length=1000)
    formats: Optional[list[str]] = PydanticField(default=None, max_length=24)
    resources: Optional[list[StudyOfferResourceReference]] = PydanticField(
        default=None, max_length=100
    )
    prerequisites: Optional[list[StudyOfferPrerequisite]] = PydanticField(
        default=None, max_length=50
    )
    accessibility_notes: Optional[list[str]] = PydanticField(
        default=None, max_length=32
    )
    effort_minutes_min: Optional[int] = PydanticField(default=None, ge=0, le=10_000_000)
    effort_minutes_max: Optional[int] = PydanticField(default=None, ge=0, le=10_000_000)
    included_services: Optional[list[str]] = PydanticField(default=None, max_length=32)
    capacity_basis: Optional[str] = PydanticField(default=None, max_length=500)
    capacity_is_limited: Optional[bool] = None
    capacity_limit: Optional[int] = PydanticField(default=None, ge=1, le=10_000_000)
    access: Optional[StudyOfferAccessTerms] = None
    assessment: Optional[StudyOfferAssessmentTerms] = None
    award: Optional[StudyOfferAwardTerms] = None
    price: Optional[StudyOfferPriceTerms] = None
    customer_terms: Optional[str] = PydanticField(default=None, max_length=12000)

    @field_validator("title", "summary", "audience", "capacity_basis", "customer_terms")
    @classmethod
    def normalize_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("goals", "formats", "accessibility_notes", "included_services")
    @classmethod
    def normalize_text_lists(cls, values: Optional[list[str]]) -> Optional[list[str]]:
        if values is None:
            return None
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("list entries cannot be blank")
        return normalized

    @field_validator("formats")
    @classmethod
    def normalize_format_codes(cls, values: Optional[list[str]]) -> Optional[list[str]]:
        if values is None:
            return None
        normalized = [value.casefold() for value in values]
        if any(
            not value.isascii()
            or not value
            or not value[0].isalpha()
            or not all(char.isalnum() or char in "_-" for char in value)
            for value in normalized
        ):
            raise ValueError("formats must be short lowercase format codes")
        return normalized

    def completion_blockers(self) -> list[str]:
        """Report absent or inconsistent proposals without making drafts sellable."""
        blockers: list[str] = []
        if not self.title:
            blockers.append("OFFER_TITLE_REQUIRED")
        if not self.goals:
            blockers.append("OFFER_GOALS_REQUIRED")
        if not self.audience:
            blockers.append("OFFER_AUDIENCE_REQUIRED")
        if not self.formats:
            blockers.append("OFFER_FORMATS_REQUIRED")
        if not self.resources:
            blockers.append("OFFER_CONTENT_REQUIRED")
        elif any(not resource.title_snapshot for resource in self.resources):
            blockers.append("OFFER_CONTENT_TITLE_SNAPSHOT_MISSING")
        if any(
            resource.kind == StudyOfferResourceKind.COURSE
            and (not resource.release_uuid or not resource.release_sha256)
            for resource in self.resources or []
        ):
            blockers.append("OFFER_COURSE_RELEASE_REQUIRED")
        course_uuids = [
            resource.resource_uuid for resource in self.resources or []
            if resource.kind == StudyOfferResourceKind.COURSE
        ]
        if len(set(course_uuids)) != len(course_uuids):
            blockers.append("OFFER_COURSE_RESOURCE_DUPLICATE")
        if any(
            prerequisite.resource is not None
            and not prerequisite.resource.title_snapshot
            for prerequisite in self.prerequisites or []
        ):
            blockers.append("OFFER_PREREQUISITE_TITLE_SNAPSHOT_MISSING")
        if self.prerequisites is None:
            blockers.append("OFFER_PREREQUISITES_UNREVIEWED")
        if self.accessibility_notes is None:
            blockers.append("OFFER_ACCESSIBILITY_UNREVIEWED")
        if self.effort_minutes_min is None or self.effort_minutes_max is None:
            blockers.append("OFFER_EFFORT_UNDEFINED")
        elif self.effort_minutes_min > self.effort_minutes_max:
            blockers.append("OFFER_EFFORT_RANGE_INVALID")
        if self.included_services is None:
            blockers.append("OFFER_INCLUDED_SERVICE_UNREVIEWED")
        if not self.capacity_basis or self.capacity_is_limited is None:
            blockers.append("OFFER_CAPACITY_BASIS_UNDEFINED")
        elif self.capacity_is_limited and self.capacity_limit is None:
            blockers.append("OFFER_CAPACITY_LIMIT_REQUIRED")
        if self.access is None or self.access.clock is None:
            blockers.append("OFFER_ACCESS_CLOCK_UNDEFINED")
        elif self.access.clock == StudyOfferAccessClock.FIXED_PERIOD:
            if (
                self.access.starts_at is None
                or self.access.ends_at is None
                or self.access.duration_days is not None
            ):
                blockers.append("OFFER_ACCESS_PERIOD_INCOMPLETE")
            elif (
                self.access.starts_at.tzinfo is None
                or self.access.ends_at.tzinfo is None
                or self.access.ends_at <= self.access.starts_at
            ):
                blockers.append("OFFER_ACCESS_PERIOD_INVALID")
        elif self.access.duration_days is None or self.access.starts_at or self.access.ends_at:
            blockers.append("OFFER_ACCESS_DURATION_INCOMPLETE")
        if self.assessment is None or self.assessment.method is None:
            blockers.append("OFFER_ASSESSMENT_TERMS_REQUIRED")
        elif (
            self.assessment.method == StudyOfferAssessmentMethod.OTHER
            and not self.assessment.description
        ):
            blockers.append("OFFER_ASSESSMENT_DESCRIPTION_REQUIRED")
        if self.award is None or self.award.kind is None:
            blockers.append("OFFER_AWARD_TERMS_REQUIRED")
        elif self.award.kind != StudyOfferAwardKind.NONE:
            if not self.award.description:
                blockers.append("OFFER_AWARD_DESCRIPTION_REQUIRED")
            # Managed award issuance cannot yet bind reviewed conditions to retained evidence.
            blockers.append("OFFER_MANAGED_AWARD_UNAVAILABLE")
        if self.price is None or (
            self.price.currency is None
            and not self.price.lines
            and self.price.total_minor is None
        ):
            blockers.append("OFFER_PRICE_TERMS_UNDEFINED")
        elif not self.price.is_complete():
            blockers.append("OFFER_PRICE_BREAKDOWN_INCOMPLETE")
        if not self.customer_terms:
            blockers.append("OFFER_CUSTOMER_TERMS_REQUIRED")
        return blockers


class StudyOffer(SQLModel, table=True):
    """An organization-scoped draft root; no publication state is available."""

    __tablename__ = "st_offer"
    __table_args__ = (
        Index("ix_st_offer_org_status", "org_id", "status"),
        Index("ix_st_offer_uuid", "offer_uuid", unique=True),
        UniqueConstraint("org_id", "id", name="uq_st_offer_org_id"),
        CheckConstraint("status = 'draft'", name="ck_st_offer_draft_only"),
        CheckConstraint(
            "offer_kind IN ('course', 'programme', 'membership')",
            name="ck_st_offer_kind",
        ),
        CheckConstraint("current_version_no >= 1", name="ck_st_offer_version_positive"),
    )

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True),
    )
    offer_uuid: str = Field(sa_column=Column(String(100), nullable=False))
    org_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
        )
    )
    offer_kind: str = Field(sa_column=Column(String(24), nullable=False))
    status: str = Field(
        default="draft",
        sa_column=Column(String(16), nullable=False, default="draft", server_default="draft"),
    )
    current_version_no: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, default=1, server_default="1"),
    )
    created_by_user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class StudyOfferVersion(SQLModel, table=True):
    """Append-only snapshot of every complete proposed draft document."""

    __tablename__ = "st_offer_version"
    __table_args__ = (
        Index("ix_st_offer_version_uuid", "version_uuid", unique=True),
        Index("ix_st_offer_version_offer_created", "offer_id", "version_no"),
        UniqueConstraint("offer_id", "version_no", name="uq_st_offer_version_sequence"),
        CheckConstraint("version_no >= 1", name="ck_st_offer_version_number_positive"),
        CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_st_offer_version_sha256_length",
        ),
    )

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True),
    )
    version_uuid: str = Field(
        default_factory=lambda: str(uuid4()),
        sa_column=Column(String(100), nullable=False),
    )
    offer_id: int = Field(
        sa_column=Column(
            BigInteger().with_variant(Integer, "sqlite"),
            ForeignKey("st_offer.id", ondelete="RESTRICT"),
            nullable=False,
        )
    )
    version_no: int = Field(sa_column=Column(Integer, nullable=False))
    terms: dict = Field(sa_column=Column(JSON, nullable=False))
    content_sha256: str = Field(sa_column=Column(String(64), nullable=False))
    created_by_user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )


class StudyOfferDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offer_uuid: str
    offer_kind: StudyOfferKind
    terms: StudyOfferTerms

    @field_validator("offer_uuid")
    @classmethod
    def normalize_offer_uuid(cls, value: str) -> str:
        try:
            return str(UUID(value.strip()))
        except (ValueError, AttributeError) as error:
            raise ValueError("offer_uuid must be a UUID") from error


class StudyOfferDraftRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = PydanticField(ge=1)
    terms: StudyOfferTerms


class StudyOfferDraftRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offer_uuid: str
    offer_kind: StudyOfferKind
    status: str
    version_no: int
    current_version: int
    current_version_uuid: str
    content_sha256: str
    terms: StudyOfferTerms
    validation_blockers: list[str]
    created_at: datetime
