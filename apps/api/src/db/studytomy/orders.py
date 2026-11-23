"""Order drafts and their named learner allocations."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel

from src.db.studytomy.governance import _utcnow


class StudyOrder(SQLModel, table=True):
    __tablename__ = "st_order"
    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_st_order_org_id"),
        UniqueConstraint("org_id", "customer_ref", name="uq_st_order_customer_ref"),
        CheckConstraint("total_minor >= 0", name="ck_st_order_total"),
        CheckConstraint("length(currency) = 3", name="ck_st_order_currency"),
        CheckConstraint(
            "(confirmation_sha256 IS NULL AND sealed_at IS NULL) OR "
            "(confirmation_sha256 IS NOT NULL AND length(confirmation_sha256) = 64)",
            name="ck_st_order_confirmation_digest",
        ),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger().with_variant(Integer, "sqlite"))
    org_id: int = Field(foreign_key="organization.id", ondelete="RESTRICT")
    purchaser_user_id: int = Field(foreign_key="user.id", ondelete="RESTRICT")
    customer_ref: str = Field(default_factory=lambda: str(uuid4()), max_length=100)
    currency: str = Field(max_length=3)
    total_minor: int = Field(sa_type=BigInteger)
    confirmation_sha256: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=True))
    sealed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class StudyOrderLine(SQLModel, table=True):
    __tablename__ = "st_order_line"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "order_id"], ["st_order.org_id", "st_order.id"],
            name="fk_st_order_line_order", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["org_id", "learner_profile_id"], ["st_learner_profile.org_id", "st_learner_profile.id"],
            name="fk_st_order_line_learner", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["org_id", "offer_id"], ["st_offer.org_id", "st_offer.id"],
            name="fk_st_order_line_offer", ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["offer_id", "offer_version_no"], ["st_offer_version.offer_id", "st_offer_version.version_no"],
            name="fk_st_order_line_version", ondelete="RESTRICT",
        ),
        UniqueConstraint("order_id", "learner_profile_id", "offer_id", name="uq_st_order_line_allocation"),
        CheckConstraint("total_minor >= 0", name="ck_st_order_line_total"),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger().with_variant(Integer, "sqlite"))
    org_id: int
    order_id: int = Field(sa_type=BigInteger)
    learner_profile_id: int = Field(sa_type=BigInteger)
    offer_id: int = Field(sa_type=BigInteger)
    offer_version_no: int
    total_minor: int = Field(sa_type=BigInteger)
    terms_snapshot: dict = Field(sa_type=JSON)
    created_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=True))


class StudyOrderAcceptance(SQLModel, table=True):
    """One retained purchaser decision for a sealed order draft."""

    __tablename__ = "st_order_acceptance"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "order_id"], ["st_order.org_id", "st_order.id"],
            name="fk_st_order_acceptance_order", ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(confirmation_sha256) = 64", name="ck_st_order_acceptance_digest",
        ),
    )

    order_id: int = Field(primary_key=True, sa_type=BigInteger)
    org_id: int
    purchaser_user_id: int = Field(foreign_key="user.id", ondelete="RESTRICT")
    confirmation_sha256: str = Field(max_length=64)
    policy_binding: dict = Field(sa_type=JSON)
    acknowledged_overlap: bool
    accepted_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=True))
