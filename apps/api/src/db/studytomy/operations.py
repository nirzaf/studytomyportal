"""Durable operation records: replay protection, delivery outbox, private evidence.

These rows exist so a retried request, a restarted worker or a lost response can
never silently become a second grant, a second charge or a second disclosure.
They are operational records, not business authority: the governed decision still
lives in the domain row and in ``st_business_event``.
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)
from pydantic import BaseModel, ConfigDict
from sqlmodel import Field, SQLModel

from src.db.studytomy.governance import _utcnow


class StudyIdempotencyStatus:
    """String statuses for ``st_idempotency_record`` (never a native enum).

    There is deliberately no ``failed`` state: a mutation that fails rolls its
    claim back with it, so only a live claim and a replayable result exist.
    """

    PROCESSING = "processing"
    COMPLETED = "completed"


class StudyIdempotencyRecord(SQLModel, table=True):
    """One replay guard per actor, organization, operation and idempotency key.

    The key itself is never stored, only ``key_hash``, so a client-chosen key
    (which may echo a reference the client already uses elsewhere) cannot leak
    into backups or support exports. While a claim is ``processing`` its
    ``expires_at`` is the lease deadline a crashed request leaves behind; once
    ``completed`` the same column is the replay-retention horizon. The row is a
    replay aid only: the domain's own resource-level uniqueness constraints stay
    authoritative after it goes.
    """

    __tablename__ = "st_idempotency_record"
    __table_args__ = (
        Index("ix_st_idempotency_record_uuid", "record_uuid", unique=True),
        Index("ix_st_idempotency_org_created", "org_id", "created_at"),
        Index("ix_st_idempotency_status_expiry", "status", "expires_at"),
        Index("ix_st_idempotency_resource", "org_id", "resource_kind", "resource_uuid"),
        UniqueConstraint(
            "org_id",
            "actor_user_id",
            "operation",
            "key_hash",
            name="uq_st_idempotency_actor_operation_key",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    record_uuid: str = Field(
        default_factory=lambda: str(uuid4()),
        sa_column=Column(String(100), nullable=False),
    )
    org_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
        )
    )
    actor_user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    operation: str = Field(sa_column=Column(String(100), nullable=False))
    key_hash: str = Field(sa_column=Column(String(64), nullable=False))
    request_hash: str = Field(sa_column=Column(String(64), nullable=False))
    status: str = Field(
        default=StudyIdempotencyStatus.PROCESSING,
        sa_column=Column(
            String(16), nullable=False, server_default=StudyIdempotencyStatus.PROCESSING
        ),
    )
    resource_kind: Optional[str] = Field(default=None, sa_column=Column(String(32)))
    resource_uuid: Optional[str] = Field(default=None, sa_column=Column(String(100)))
    response_status: Optional[int] = Field(default=None, sa_column=Column(Integer))
    response_projection: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    completed_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    expires_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )


class StudyOutboxPurpose(str, Enum):
    """Why a delivery exists. Mirrors the ``ck_st_outbox_purpose`` values."""

    REQUIRED_NOTICE = "required_notice"
    RECEIPT = "receipt"
    PROGRESS_REPORT = "progress_report"
    OPTIONAL_REMINDER = "optional_reminder"
    MARKETING = "marketing"


class StudyOutboxTemplate(str, Enum):
    """An allowlisted template key, never message content. Mirrors the DB check."""

    REQUIRED_NOTICE = "required_notice_v1"
    RECEIPT = "receipt_v1"
    PROGRESS_REPORT = "progress_report_v1"
    OPTIONAL_REMINDER = "optional_reminder_v1"
    MARKETING = "marketing_v1"


class StudyOutboxStatus:
    """String statuses for ``st_outbox_record`` (never a native enum).

    ``reconciliation_required`` is a first-class outcome: an expired send lease
    means the provider may have taken the message, so the record must never be
    replayed automatically or reported as delivered.
    """

    QUEUED = "queued"
    LEASED = "leased"
    RETRY = "retry"
    SENT = "sent"
    SUPPRESSED = "suppressed"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    DEAD_LETTER = "dead_letter"


class StudyOutboxRecord(SQLModel, table=True):
    """Delivery intent written in the same transaction as the decision that caused it.

    Only an allowlisted template key, opaque resource references and hashes are
    stored: no recipient address, no rendered body, no consent text, no raw
    dedupe key. Rendering and recipient resolution happen in the worker against
    current authorized rows, which is why a stored record is never a promise.
    """

    __tablename__ = "st_outbox_record"
    __table_args__ = (
        Index("ix_st_outbox_record_uuid", "record_uuid", unique=True),
        Index("ix_st_outbox_due", "status", "next_attempt_at", "created_at"),
        Index("ix_st_outbox_recipient", "org_id", "recipient_user_id", "created_at"),
        Index("ix_st_outbox_resource", "org_id", "resource_kind", "resource_uuid"),
        Index("ix_st_outbox_lease", "status", "lease_expires_at"),
        UniqueConstraint("org_id", "dedupe_hash", name="uq_st_outbox_org_dedupe"),
        UniqueConstraint(
            "provider_idempotency_key", name="uq_st_outbox_provider_idempotency"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    record_uuid: str = Field(
        default_factory=lambda: str(uuid4()),
        sa_column=Column(String(100), nullable=False),
    )
    org_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
        )
    )
    recipient_user_id: int = Field(
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False)
    )
    purpose: str = Field(sa_column=Column(String(32), nullable=False))
    template_key: str = Field(sa_column=Column(String(80), nullable=False))
    resource_kind: str = Field(sa_column=Column(String(32), nullable=False))
    resource_uuid: str = Field(sa_column=Column(String(100), nullable=False))
    resource_version: Optional[int] = Field(default=None, sa_column=Column(Integer))
    authority_reference_uuid: Optional[str] = Field(
        default=None, sa_column=Column(String(100))
    )
    policy_refs: list = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False),
    )
    dedupe_hash: str = Field(sa_column=Column(String(64), nullable=False))
    request_hash: str = Field(sa_column=Column(String(64), nullable=False))
    # Generated once, when intent is recorded, and reused by every attempt at
    # this delivery: resending the same record must not become a second message.
    provider_idempotency_key: str = Field(
        default_factory=lambda: str(uuid4()),
        sa_column=Column(String(64), nullable=False),
    )
    status: str = Field(
        default=StudyOutboxStatus.QUEUED,
        sa_column=Column(String(32), nullable=False, server_default=StudyOutboxStatus.QUEUED),
    )
    attempt_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default="0")
    )
    next_attempt_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    lease_owner: Optional[str] = Field(default=None, sa_column=Column(String(100)))
    lease_expires_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )
    provider_message_reference: Optional[str] = Field(
        default=None, sa_column=Column(String(160))
    )
    last_error_code: Optional[str] = Field(default=None, sa_column=Column(String(64)))
    created_by_user_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("user.id", ondelete="RESTRICT")),
    )
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    completed_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )


class StudyEvidenceFile(SQLModel, table=True):
    """Private metadata sidecar for evidence bytes held by the storage adapter."""

    __tablename__ = "st_evidence_file"
    __table_args__ = (
        Index("ix_st_evidence_file_uuid", "evidence_uuid", unique=True),
        Index(
            "ix_st_evidence_file_org_learner",
            "org_id",
            "learner_profile_id",
            "created_at",
        ),
        CheckConstraint(
            "purpose IN ('eligibility_evidence', 'content_rights', 'content_accessibility', 'record_request_evidence')",
            name="ck_st_evidence_file_purpose",
        ),
        CheckConstraint(
            "(purpose = 'eligibility_evidence' AND learner_profile_id IS NOT NULL "
            "AND course_uuid IS NULL AND revision_sha256 IS NULL AND record_request_event_uuid IS NULL) OR "
            "(purpose IN ('content_rights', 'content_accessibility') AND learner_profile_id IS NULL "
            "AND course_uuid IS NOT NULL AND revision_sha256 IS NOT NULL AND length(revision_sha256) = 64 "
            "AND record_request_event_uuid IS NULL) OR "
            "(purpose = 'record_request_evidence' AND learner_profile_id IS NOT NULL "
            "AND course_uuid IS NULL AND revision_sha256 IS NULL AND record_request_event_uuid IS NOT NULL)",
            name="ck_st_evidence_file_scope",
        ),
        Index("ix_st_evidence_file_course", "org_id", "course_uuid", "revision_sha256"),
        Index("ix_st_evidence_file_record_request", "org_id", "record_request_event_uuid"),
        ForeignKeyConstraint(
            ["org_id", "record_request_event_uuid"], ["st_business_event.org_id", "st_business_event.event_uuid"],
            name="fk_st_evidence_file_record_request", ondelete="RESTRICT",
        ),
        CheckConstraint(
            "upload_status IN ('uploading', 'ready', 'upload_failed', 'reconciliation_required')",
            name="ck_st_evidence_file_status",
        ),
        CheckConstraint("size_bytes > 0", name="ck_st_evidence_file_size_positive"),
        CheckConstraint(
            "length(content_sha256) = 64", name="ck_st_evidence_file_sha256_length"
        ),
    )

    id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger().with_variant(Integer(), "sqlite"), primary_key=True
        ),
    )
    evidence_uuid: str = Field(sa_column=Column(String(100), nullable=False))
    org_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("organization.id", ondelete="RESTRICT"), nullable=False
        )
    )
    learner_profile_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            BigInteger().with_variant(Integer(), "sqlite"),
            ForeignKey("st_learner_profile.id", ondelete="RESTRICT"),
            nullable=True,
        )
    )
    course_uuid: Optional[str] = Field(default=None, sa_column=Column(String(100)))
    revision_sha256: Optional[str] = Field(default=None, sa_column=Column(String(64)))
    record_request_event_uuid: Optional[str] = Field(default=None, sa_column=Column(String(100)))
    purpose: str = Field(sa_column=Column(String(32), nullable=False))
    storage_reference: str = Field(sa_column=Column(String(700), nullable=False))
    content_sha256: str = Field(sa_column=Column(String(64), nullable=False))
    policy_version_ids: list = Field(
        default_factory=list, sa_column=Column(JSON, nullable=False)
    )
    media_type: str = Field(sa_column=Column(String(100), nullable=False))
    size_bytes: int = Field(sa_column=Column(Integer, nullable=False))
    retention_category: str = Field(sa_column=Column(String(64), nullable=False))
    upload_status: str = Field(sa_column=Column(String(32), nullable=False))
    uploaded_by_user_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("user.id", ondelete="RESTRICT"), nullable=False
        )
    )
    last_error_code: Optional[str] = Field(default=None, sa_column=Column(String(64)))
    created_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=_utcnow,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    verified_at: Optional[datetime] = Field(
        default=None, sa_column=Column(DateTime(timezone=True))
    )


class StudyEvidenceFileRead(BaseModel):
    """Safe evidence metadata projection; storage references stay internal."""

    model_config = ConfigDict(extra="forbid")

    evidence_uuid: str
    learner_uuid: str
    purpose: str
    content_sha256: str
    policy_version_ids: list[int]
    media_type: str
    size_bytes: int
    retention_category: str
    upload_status: str
    created_at: datetime
    updated_at: datetime
    verified_at: Optional[datetime] = None
