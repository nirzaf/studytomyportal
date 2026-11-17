"""Private adaptive rounds, response ownership, evidence journal and projections."""

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Numeric,
    UniqueConstraint,
    text,
)
from sqlmodel import Field

from src.db.studytomy.adaptive import CurriculumRecord
from src.db.studytomy.governance import _utcnow


class StudyLearningAttempt(CurriculumRecord, table=True):
    __tablename__ = "st_learning_attempt"
    __table_args__ = (
        ForeignKeyConstraint(
            ("org_id", "learner_profile_id"),
            ("st_learner_profile.org_id", "st_learner_profile.id"),
            name="fk_st_attempt_learner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("org_id", "curriculum_revision_id"),
            ("st_curriculum_revision.org_id", "st_curriculum_revision.id"),
            name="fk_st_attempt_curriculum",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "org_id",
                "curriculum_revision_id",
                "diagnostic_revision_id",
                "blueprint_uuid",
            ),
            (
                "st_diagnostic_revision.org_id",
                "st_diagnostic_revision.curriculum_revision_id",
                "st_diagnostic_revision.id",
                "st_diagnostic_revision.blueprint_uuid",
            ),
            name="fk_st_attempt_diagnostic",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("uuid", name="uq_st_attempt_uuid"),
        UniqueConstraint(
            "org_id",
            "id",
            "learner_profile_id",
            "curriculum_revision_id",
            name="uq_st_attempt_item_binding",
        ),
        UniqueConstraint(
            "org_id", "source_round_uuid", name="uq_st_attempt_source_round"
        ),
        UniqueConstraint(
            "org_id",
            "source_submission_uuid",
            "source_attempt_number",
            name="uq_st_attempt_native_round",
        ),
        Index(
            "uq_st_attempt_active_diagnostic",
            "org_id",
            "learner_profile_id",
            "blueprint_uuid",
            unique=True,
            postgresql_where=text("kind = 'diagnostic' AND status = 'active'"),
            sqlite_where=text("kind = 'diagnostic' AND status = 'active'"),
        ),
        CheckConstraint(
            "(kind = 'diagnostic' AND diagnostic_revision_id IS NOT NULL AND blueprint_uuid IS NOT NULL) OR "
            "(kind IN ('practice', 'assignment') AND diagnostic_revision_id IS NULL AND blueprint_uuid IS NULL)",
            name="ck_st_attempt_kind",
        ),
        CheckConstraint(
            "(kind = 'assignment' AND source_round_uuid IS NOT NULL AND source_assignment_uuid IS NOT NULL "
            "AND source_submission_uuid IS NOT NULL AND source_attempt_number IS NOT NULL AND source_attempt_number >= 1) OR "
            "(kind <> 'assignment' AND source_round_uuid IS NULL AND source_assignment_uuid IS NULL "
            "AND source_submission_uuid IS NULL AND source_attempt_number IS NULL)",
            name="ck_st_attempt_round",
        ),
        CheckConstraint(
            "(status = 'active' AND ended_at IS NULL) OR "
            "(status IN ('completed', 'abandoned', 'expired', 'invalidated') AND ended_at IS NOT NULL AND ended_at >= started_at)",
            name="ck_st_attempt_status",
        ),
        CheckConstraint(
            "expires_at > started_at AND version >= 1 AND next_ordinal >= 1 AND length(manifest_digest) = 64",
            name="ck_st_attempt_version",
        ),
        CheckConstraint(
            "(actor_capacity IN ('learner', 'reviewer') AND actor_user_id IS NOT NULL) OR "
            "(actor_capacity IN ('delegated', 'system') AND actor_reference IS NOT NULL)",
            name="ck_st_attempt_actor",
        ),
    )

    uuid: str = Field(default_factory=lambda: f"attempt_{uuid4()}", max_length=64)
    learner_profile_id: int = Field(sa_type=BigInteger)
    curriculum_revision_id: int = Field(sa_type=BigInteger)
    diagnostic_revision_id: int | None = Field(default=None, sa_type=BigInteger)
    blueprint_uuid: str | None = Field(default=None, max_length=64)
    actor_user_id: int | None = Field(
        default=None, foreign_key="user.id", ondelete="RESTRICT"
    )
    actor_capacity: str = Field(max_length=16)
    actor_reference: str | None = Field(default=None, max_length=100)
    kind: str = Field(max_length=12)
    status: str = Field(default="active", max_length=12)
    version: int = 1
    next_ordinal: int = 1
    source_course_uuid: str = Field(max_length=255)
    source_activity_uuid: str | None = Field(default=None, max_length=89)
    source_round_uuid: str | None = Field(default=None, max_length=64)
    source_assignment_uuid: str | None = Field(default=None, max_length=100)
    source_submission_uuid: str | None = Field(default=None, max_length=100)
    source_attempt_number: int | None = None
    manifest_digest: str = Field(max_length=64)
    policy_version_ids: list[int] = Field(default_factory=list, sa_type=JSON)
    started_at: datetime = Field(
        default_factory=_utcnow, sa_type=DateTime(timezone=True)
    )
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    ended_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class StudyAttemptItem(CurriculumRecord, table=True):
    __tablename__ = "st_attempt_item"
    __table_args__ = (
        ForeignKeyConstraint(
            ("org_id", "attempt_id", "learner_profile_id", "curriculum_revision_id"),
            (
                "st_learning_attempt.org_id",
                "st_learning_attempt.id",
                "st_learning_attempt.learner_profile_id",
                "st_learning_attempt.curriculum_revision_id",
            ),
            name="fk_st_attempt_item_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            (
                "org_id",
                "curriculum_revision_id",
                "concept_id",
                "item_revision_id",
                "item_family_uuid",
            ),
            (
                "st_item_revision.org_id",
                "st_item_revision.curriculum_revision_id",
                "st_item_revision.concept_id",
                "st_item_revision.id",
                "st_item_revision.item_family_uuid",
            ),
            name="fk_st_attempt_item_definition",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("uuid", name="uq_st_attempt_item_uuid"),
        UniqueConstraint("attempt_id", "ordinal", name="uq_st_attempt_item_ordinal"),
        UniqueConstraint(
            "org_id",
            "id",
            "learner_profile_id",
            "curriculum_revision_id",
            "concept_id",
            "uuid",
            name="uq_st_attempt_item_evidence_binding",
        ),
        Index(
            "uq_st_attempt_item_outstanding",
            "attempt_id",
            unique=True,
            postgresql_where=text("is_diagnostic = true AND response_state = 'issued'"),
            sqlite_where=text("is_diagnostic = true AND response_state = 'issued'"),
        ),
        Index(
            "uq_st_attempt_diagnostic_item_family",
            "attempt_id",
            "item_family_uuid",
            unique=True,
            postgresql_where=text("is_diagnostic = true"),
            sqlite_where=text("is_diagnostic = true"),
        ),
        CheckConstraint(
            "ordinal >= 1 AND response_version >= 1 AND help_count >= 0 AND exposure_count >= 0",
            name="ck_st_attempt_item_version",
        ),
        CheckConstraint(
            "NOT is_diagnostic OR help_count = 0",
            name="ck_st_attempt_item_diagnostic_help",
        ),
        CheckConstraint(
            "(response_state = 'issued' AND normalized_response IS NULL AND response_digest IS NULL "
            "AND responded_at IS NULL AND skip_reason IS NULL) OR "
            "(response_state = 'answered' AND normalized_response IS NOT NULL AND response_digest IS NOT NULL "
            "AND length(response_digest) = 64 AND responded_at IS NOT NULL AND responded_at >= issued_at AND skip_reason IS NULL) OR "
            "(response_state = 'skipped' AND normalized_response IS NULL AND response_digest IS NULL "
            "AND responded_at IS NOT NULL AND responded_at >= issued_at AND skip_reason IS NOT NULL)",
            name="ck_st_attempt_item_response",
        ),
    )

    uuid: str = Field(default_factory=lambda: f"issued_{uuid4()}", max_length=64)
    attempt_id: int = Field(sa_type=BigInteger)
    learner_profile_id: int = Field(sa_type=BigInteger)
    curriculum_revision_id: int = Field(sa_type=BigInteger)
    concept_id: int = Field(sa_type=BigInteger)
    item_revision_id: int = Field(sa_type=BigInteger)
    item_family_uuid: str = Field(max_length=64)
    ordinal: int
    is_diagnostic: bool
    issued_at: datetime = Field(
        default_factory=_utcnow, sa_type=DateTime(timezone=True)
    )
    source_locator: str | None = Field(default=None, max_length=100)
    response_state: str = Field(default="issued", max_length=12)
    response_version: int = 1
    normalized_response: str | None = Field(default=None, max_length=128)
    responded_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    response_digest: str | None = Field(default=None, max_length=64)
    help_count: int = 0
    exposure_count: int = 0
    skip_reason: str | None = Field(default=None, max_length=40)


class StudyLearningEvidence(CurriculumRecord, table=True):
    __tablename__ = "st_learning_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            (
                "org_id",
                "attempt_item_id",
                "learner_profile_id",
                "curriculum_revision_id",
                "concept_id",
                "logical_source_uuid",
            ),
            (
                "st_attempt_item.org_id",
                "st_attempt_item.id",
                "st_attempt_item.learner_profile_id",
                "st_attempt_item.curriculum_revision_id",
                "st_attempt_item.concept_id",
                "st_attempt_item.uuid",
            ),
            name="fk_st_evidence_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("org_id", "learner_profile_id", "logical_source_uuid", "supersedes_id"),
            (
                "st_learning_evidence.org_id",
                "st_learning_evidence.learner_profile_id",
                "st_learning_evidence.logical_source_uuid",
                "st_learning_evidence.id",
            ),
            name="fk_st_evidence_predecessor",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("uuid", name="uq_st_evidence_uuid"),
        UniqueConstraint(
            "org_id",
            "learner_profile_id",
            "evidence_sequence",
            name="uq_st_evidence_sequence",
        ),
        UniqueConstraint(
            "logical_source_uuid", "result_revision", name="uq_st_evidence_revision"
        ),
        UniqueConstraint(
            "org_id",
            "learner_profile_id",
            "logical_source_uuid",
            "id",
            name="uq_st_evidence_binding",
        ),
        UniqueConstraint("supersedes_id", name="uq_st_evidence_successor"),
        Index(
            "ix_st_evidence_concept_sequence",
            "org_id",
            "learner_profile_id",
            "concept_id",
            "evidence_sequence",
        ),
        CheckConstraint(
            "evidence_sequence >= 1 AND result_revision >= 1",
            name="ck_st_evidence_revision",
        ),
        CheckConstraint(
            "(result_revision = 1 AND supersedes_id IS NULL AND correction_reason IS NULL AND NOT is_retraction) OR "
            "(result_revision > 1 AND supersedes_id IS NOT NULL AND correction_reason IS NOT NULL AND supersedes_id <> id)",
            name="ck_st_evidence_correction",
        ),
        CheckConstraint(
            "(outcome = 'correct' AND score = 1 AND score IS NOT NULL) OR "
            "(outcome = 'incorrect' AND score = 0 AND score IS NOT NULL) OR "
            "(outcome = 'not_scored' AND score IS NULL)",
            name="ck_st_evidence_score",
        ),
        CheckConstraint(
            "independence IN ('independent', 'assisted', 'unknown') AND "
            "(NOT eligible OR (independence = 'independent' AND outcome IN ('correct', 'incorrect') AND NOT is_retraction)) "
            "AND (NOT is_retraction OR outcome = 'not_scored')",
            name="ck_st_evidence_eligibility",
        ),
        CheckConstraint(
            "(actor_user_id IS NULL) <> (actor_reference IS NULL)",
            name="ck_st_evidence_actor",
        ),
    )

    uuid: str = Field(default_factory=lambda: f"evidence_{uuid4()}", max_length=64)
    learner_profile_id: int = Field(sa_type=BigInteger)
    curriculum_revision_id: int = Field(sa_type=BigInteger)
    concept_id: int = Field(sa_type=BigInteger)
    attempt_item_id: int = Field(sa_type=BigInteger)
    logical_source_uuid: str = Field(max_length=64)
    evidence_sequence: int | None = Field(
        default=None,
        sa_type=BigInteger,
        nullable=False,
        sa_column_kwargs={"server_default": text("0")},
    )
    result_revision: int = 1
    evaluator_version: str = Field(max_length=40)
    policy_version_ids: list[int] = Field(default_factory=list, sa_type=JSON)
    outcome: str = Field(max_length=12)
    score: int | None = None
    independence: str = Field(max_length=12)
    eligible: bool
    eligibility_reason: str = Field(max_length=64)
    misconception_code: str | None = Field(default=None, max_length=64)
    observed_at: datetime = Field(sa_type=DateTime(timezone=True))
    actor_user_id: int | None = Field(
        default=None, foreign_key="user.id", ondelete="RESTRICT"
    )
    actor_reference: str | None = Field(default=None, max_length=100)
    supersedes_id: int | None = Field(default=None, sa_type=BigInteger)
    correction_reason: str | None = Field(default=None, max_length=64)
    is_retraction: bool = False


class StudyConceptMastery(CurriculumRecord, table=True):
    __tablename__ = "st_concept_mastery"
    __table_args__ = (
        ForeignKeyConstraint(
            ("org_id", "learner_profile_id"),
            ("st_learner_profile.org_id", "st_learner_profile.id"),
            name="fk_st_mastery_learner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ("org_id", "curriculum_revision_id", "concept_id"),
            ("st_concept.org_id", "st_concept.curriculum_revision_id", "st_concept.id"),
            name="fk_st_mastery_concept",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("uuid", name="uq_st_mastery_uuid"),
        UniqueConstraint(
            "org_id",
            "learner_profile_id",
            "concept_id",
            "mastery_policy_digest",
            name="uq_st_mastery_projection",
        ),
        CheckConstraint(
            "evidence_watermark >= 0 AND version >= 1 AND success_count BETWEEN 0 AND eligible_count "
            "AND assisted_count >= 0 AND unknown_count >= 0 AND length(mastery_policy_digest) = 64",
            name="ck_st_mastery_counts",
        ),
        CheckConstraint(
            "(eligible_count = 0 AND state = 'not_assessed' AND score IS NULL AND latest_independent_at IS NULL) OR "
            "(eligible_count > 0 AND state IN ('insufficient_evidence', 'developing', 'mastered') "
            "AND score IS NOT NULL AND score BETWEEN 0 AND 1 AND latest_independent_at IS NOT NULL)",
            name="ck_st_mastery_score",
        ),
    )

    uuid: str = Field(default_factory=lambda: f"mastery_{uuid4()}", max_length=64)
    learner_profile_id: int = Field(sa_type=BigInteger)
    curriculum_revision_id: int = Field(sa_type=BigInteger)
    concept_id: int = Field(sa_type=BigInteger)
    mastery_policy_uuid: str = Field(max_length=100)
    mastery_policy_digest: str = Field(max_length=64)
    reducer_version: str = Field(max_length=40)
    evidence_watermark: int = Field(sa_type=BigInteger)
    version: int = 1
    state: str = Field(max_length=24)
    eligible_count: int
    success_count: int
    assisted_count: int
    unknown_count: int
    score: Decimal | None = Field(default=None, sa_type=Numeric(10, 9))
    latest_independent_at: datetime | None = Field(
        default=None, sa_type=DateTime(timezone=True)
    )
    review_due: bool = False
    computed_at: datetime = Field(
        default_factory=_utcnow, sa_type=DateTime(timezone=True)
    )
