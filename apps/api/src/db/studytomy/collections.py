"""Payment attempts and confirmed collection evidence."""

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


class StudyPaymentAttempt(SQLModel, table=True):
    __tablename__ = "st_payment_attempt"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "order_id"], ["st_order.org_id", "st_order.id"],
            name="fk_st_payment_attempt_order", ondelete="RESTRICT",
        ),
        UniqueConstraint("org_id", "order_id", "id", name="uq_st_payment_attempt_scope"),
        UniqueConstraint("org_id", "method", "attempt_reference", name="uq_st_payment_attempt_reference"),
        CheckConstraint("method IN ('manual', 'gateway')", name="ck_st_payment_attempt_method"),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger().with_variant(Integer, "sqlite"))
    org_id: int
    order_id: int = Field(sa_type=BigInteger, foreign_key="st_order_acceptance.order_id", ondelete="RESTRICT")
    method: str = Field(max_length=16)
    attempt_reference: str = Field(max_length=100)
    created_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=True))


class StudyMoneyMovement(SQLModel, table=True):
    """Positive confirmed money, distinct from attempted payment and access."""

    __tablename__ = "st_money_movement"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "order_id", "attempt_id"],
            ["st_payment_attempt.org_id", "st_payment_attempt.order_id", "st_payment_attempt.id"],
            name="fk_st_money_movement_attempt", ondelete="RESTRICT",
        ),
        UniqueConstraint("attempt_id", name="uq_st_money_movement_attempt"),
        UniqueConstraint("org_id", "order_id", "id", "currency", name="uq_st_money_movement_refund_scope"),
        UniqueConstraint("org_id", "external_reference", name="uq_st_money_movement_external_reference"),
        CheckConstraint("amount_minor > 0", name="ck_st_money_movement_amount"),
        CheckConstraint("length(currency) = 3", name="ck_st_money_movement_currency"),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger().with_variant(Integer, "sqlite"))
    org_id: int
    order_id: int = Field(sa_type=BigInteger)
    attempt_id: int = Field(sa_type=BigInteger)
    amount_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(max_length=3)
    external_reference: str = Field(max_length=150)
    evidence_reference: str = Field(max_length=150)
    confirmed_by_user_id: int | None = Field(default=None, foreign_key="user.id", ondelete="RESTRICT")
    confirmed_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=True))


class StudyManualCollectionEvidence(SQLModel, table=True):
    """A retained finance review of a settled bank record."""

    __tablename__ = "st_manual_collection_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "order_id", "attempt_id"],
            ["st_payment_attempt.org_id", "st_payment_attempt.order_id", "st_payment_attempt.id"],
            name="fk_st_manual_evidence_attempt", ondelete="RESTRICT",
        ),
        UniqueConstraint("org_id", "external_reference", name="uq_st_manual_evidence_external_reference"),
        CheckConstraint("amount_minor > 0", name="ck_st_manual_evidence_amount"),
        CheckConstraint("length(currency) = 3", name="ck_st_manual_evidence_currency"),
        CheckConstraint("length(record_sha256) = 64", name="ck_st_manual_evidence_digest"),
        CheckConstraint("settled_at <= verified_at", name="ck_st_manual_evidence_time"),
    )

    attempt_id: int = Field(primary_key=True, sa_type=BigInteger)
    org_id: int
    order_id: int = Field(sa_type=BigInteger)
    external_reference: str = Field(max_length=150)
    evidence_reference: str = Field(max_length=150)
    record_sha256: str = Field(max_length=64)
    amount_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(max_length=3)
    settled_at: datetime = Field(sa_type=DateTime(timezone=True))
    verified_by_user_id: int = Field(foreign_key="user.id", ondelete="RESTRICT")
    verified_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=True))


class StudyCollectionRemedy(SQLModel, table=True):
    """An open finance review linked to one retained bank record."""

    __tablename__ = "st_collection_remedy"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "order_id", "attempt_id"],
            ["st_payment_attempt.org_id", "st_payment_attempt.order_id", "st_payment_attempt.id"],
            name="fk_st_collection_remedy_attempt", ondelete="RESTRICT",
        ),
        CheckConstraint(
            "reason IN ('amount_or_currency_mismatch', 'additional_collection', 'capacity_or_sale_unavailable')",
            name="ck_st_collection_remedy_reason",
        ),
    )

    attempt_id: int = Field(
        primary_key=True, sa_type=BigInteger,
        foreign_key="st_manual_collection_evidence.attempt_id", ondelete="RESTRICT",
    )
    org_id: int
    order_id: int = Field(sa_type=BigInteger)
    reason: str = Field(max_length=32)
    opened_by_user_id: int = Field(foreign_key="user.id", ondelete="RESTRICT")
    opened_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=True))


class StudyCollectionResolution(SQLModel, table=True):
    """Evidence of an external remedy disposition, separate from money or access."""

    __tablename__ = "st_collection_resolution"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('returned_externally', 'settlement_not_received')",
            name="ck_st_collection_resolution_outcome",
        ),
        CheckConstraint("length(record_sha256) = 64", name="ck_st_collection_resolution_digest"),
    )

    attempt_id: int = Field(
        primary_key=True, sa_type=BigInteger,
        foreign_key="st_collection_remedy.attempt_id", ondelete="RESTRICT",
    )
    outcome: str = Field(max_length=32)
    evidence_reference: str = Field(max_length=150)
    record_sha256: str = Field(max_length=64)
    resolved_by_user_id: int = Field(foreign_key="user.id", ondelete="RESTRICT")
    resolved_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=True))


class StudyRefundReservation(SQLModel, table=True):
    """Immutable approval to reserve a portion of a confirmed collection."""

    __tablename__ = "st_refund_reservation"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "order_id", "movement_id", "currency"],
            ["st_money_movement.org_id", "st_money_movement.order_id",
             "st_money_movement.id", "st_money_movement.currency"],
            name="fk_st_refund_reservation_collection", ondelete="RESTRICT",
        ),
        UniqueConstraint("org_id", "refund_reference", name="uq_st_refund_reservation_reference"),
        UniqueConstraint("org_id", "id", name="uq_st_refund_reservation_org_id"),
        CheckConstraint("amount_minor > 0", name="ck_st_refund_reservation_amount"),
        CheckConstraint("length(reason_code) > 0", name="ck_st_refund_reservation_reason"),
        CheckConstraint("remedy_policy_id <> finance_policy_id", name="ck_st_refund_reservation_policies"),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger().with_variant(Integer, "sqlite"))
    org_id: int
    order_id: int = Field(sa_type=BigInteger)
    movement_id: int = Field(sa_type=BigInteger, index=True)
    refund_reference: str = Field(max_length=36)
    amount_minor: int = Field(sa_type=BigInteger)
    currency: str = Field(max_length=3)
    reason_code: str = Field(max_length=64)
    requested_by_user_id: int = Field(foreign_key="user.id", ondelete="RESTRICT")
    approved_by_user_id: int = Field(foreign_key="user.id", ondelete="RESTRICT")
    remedy_policy_id: int = Field(foreign_key="st_policy_version.id", ondelete="RESTRICT")
    finance_policy_id: int = Field(foreign_key="st_policy_version.id", ondelete="RESTRICT")
    approved_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=True))


class StudyRefundSettlement(SQLModel, table=True):
    """A verified external return of one exact reservation."""

    __tablename__ = "st_refund_settlement"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "reservation_id"], ["st_refund_reservation.org_id", "st_refund_reservation.id"],
            name="fk_st_refund_settlement_reservation", ondelete="RESTRICT",
        ),
        UniqueConstraint("org_id", "external_reference", name="uq_st_refund_settlement_external_reference"),
        CheckConstraint("length(external_reference) > 0", name="ck_st_refund_settlement_external_reference"),
        CheckConstraint("length(evidence_reference) > 0", name="ck_st_refund_settlement_evidence_reference"),
        CheckConstraint("length(record_sha256) = 64", name="ck_st_refund_settlement_digest"),
        CheckConstraint("settled_at <= verified_at", name="ck_st_refund_settlement_time"),
        CheckConstraint("remedy_policy_id <> finance_policy_id", name="ck_st_refund_settlement_policies"),
    )

    reservation_id: int = Field(primary_key=True, sa_type=BigInteger)
    org_id: int
    external_reference: str = Field(max_length=150)
    evidence_reference: str = Field(max_length=150)
    record_sha256: str = Field(max_length=64)
    settled_at: datetime = Field(sa_type=DateTime(timezone=True))
    verified_by_user_id: int = Field(foreign_key="user.id", ondelete="RESTRICT")
    remedy_policy_id: int = Field(foreign_key="st_policy_version.id", ondelete="RESTRICT")
    finance_policy_id: int = Field(foreign_key="st_policy_version.id", ondelete="RESTRICT")
    verified_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=True))


class StudyChargebackAdjustment(SQLModel, table=True):
    """Confirmed external debit against the original collection."""

    __tablename__ = "st_chargeback_adjustment"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "order_id", "movement_id", "currency"],
            ["st_money_movement.org_id", "st_money_movement.order_id",
             "st_money_movement.id", "st_money_movement.currency"],
            name="fk_st_chargeback_collection", ondelete="RESTRICT",
        ),
        UniqueConstraint("org_id", "external_reference", name="uq_st_chargeback_external_reference"),
        UniqueConstraint("org_id", "id", name="uq_st_chargeback_org_id"),
        CheckConstraint("amount_minor > 0", name="ck_st_chargeback_amount"),
        CheckConstraint("length(record_sha256) = 64", name="ck_st_chargeback_digest"),
        CheckConstraint("effective_at <= verified_at", name="ck_st_chargeback_time"),
    )

    id: int | None = Field(default=None, primary_key=True, sa_type=BigInteger().with_variant(Integer, "sqlite"))
    org_id: int
    order_id: int = Field(sa_type=BigInteger)
    movement_id: int = Field(sa_type=BigInteger, index=True)
    currency: str = Field(max_length=3)
    amount_minor: int = Field(sa_type=BigInteger)
    external_reference: str = Field(max_length=150)
    case_reference: str = Field(max_length=150)
    record_sha256: str = Field(max_length=64)
    effective_at: datetime = Field(sa_type=DateTime(timezone=True))
    verified_by_user_id: int = Field(foreign_key="user.id", ondelete="RESTRICT")
    remedy_policy_id: int = Field(foreign_key="st_policy_version.id", ondelete="RESTRICT")
    finance_policy_id: int = Field(foreign_key="st_policy_version.id", ondelete="RESTRICT")
    verified_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=True))


class StudyChargebackReversal(SQLModel, table=True):
    """Confirmed full reversal of one retained external debit."""

    __tablename__ = "st_chargeback_reversal"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "adjustment_id"], ["st_chargeback_adjustment.org_id", "st_chargeback_adjustment.id"],
            name="fk_st_chargeback_reversal_adjustment", ondelete="RESTRICT",
        ),
        UniqueConstraint("org_id", "external_reference", name="uq_st_chargeback_reversal_reference"),
        CheckConstraint("length(record_sha256) = 64", name="ck_st_chargeback_reversal_digest"),
        CheckConstraint("effective_at <= verified_at", name="ck_st_chargeback_reversal_time"),
    )

    adjustment_id: int = Field(primary_key=True, sa_type=BigInteger)
    org_id: int
    external_reference: str = Field(max_length=150)
    case_reference: str = Field(max_length=150)
    record_sha256: str = Field(max_length=64)
    effective_at: datetime = Field(sa_type=DateTime(timezone=True))
    verified_by_user_id: int = Field(foreign_key="user.id", ondelete="RESTRICT")
    remedy_policy_id: int = Field(foreign_key="st_policy_version.id", ondelete="RESTRICT")
    finance_policy_id: int = Field(foreign_key="st_policy_version.id", ondelete="RESTRICT")
    verified_at: datetime = Field(default_factory=_utcnow, sa_type=DateTime(timezone=True))
