"""Retained reservations for finite offer capacity."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Integer, UniqueConstraint
from sqlmodel import Field, SQLModel

from src.db.studytomy.governance import _utcnow


class StudyCapacityHold(SQLModel, table=True):
    __tablename__ = "st_capacity_hold"
    __table_args__ = (
        UniqueConstraint("offer_id", "request_uuid", name="uq_st_capacity_hold_request"),
        CheckConstraint("places > 0", name="ck_st_capacity_hold_places"),
        CheckConstraint("expires_at > created_at", name="ck_st_capacity_hold_expiry"),
        CheckConstraint(
            "consumed_at IS NULL OR (consumed_at >= created_at AND consumed_at < expires_at)",
            name="ck_st_capacity_hold_consumed",
        ),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger().with_variant(Integer, "sqlite"))
    offer_id: int = Field(
        sa_type=BigInteger, foreign_key="st_offer.id", ondelete="RESTRICT",
    )
    request_uuid: str = Field(max_length=100)
    places: int
    created_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=True))
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    consumed_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
