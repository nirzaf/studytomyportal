"""Versioned concept mastery derived only from retained learning evidence."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from src.db.studytomy.governance import StudyPolicyBody
from src.services.studytomy.errors import POLICY_NOT_APPROVED, studytomy_http_error
from src.services.studytomy.idempotency import canonical_request_hash

PolicyUUID = Annotated[str, StringConstraints(min_length=1, max_length=100)]
MasteryState = Literal[
    "not_assessed", "insufficient_evidence", "developing", "mastered"
]


class MasteryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    policy_uuid: PolicyUUID
    reducer_version: Literal["adaptive_mastery_v1"]
    window_size: Annotated[int, Field(ge=1, le=100)]
    minimum_families: Annotated[int, Field(ge=1, le=100)]
    success_threshold_percent: Annotated[int, Field(ge=1, le=100)]
    minimum_sessions: Annotated[int, Field(ge=2, le=20)]
    separation_minutes: Annotated[int, Field(ge=1, le=525_600)]
    review_after_days: Annotated[int, Field(ge=1, le=3650)]

    @model_validator(mode="after")
    def check_window(self) -> Self:
        if not self.minimum_sessions <= self.minimum_families <= self.window_size:
            raise ValueError(
                "The evidence window must support the required families and sessions."
            )
        return self

    @property
    def digest(self) -> str:
        return canonical_request_hash(self)


def resolve_mastery_policy(body: StudyPolicyBody, mastery_uuid: str) -> MasteryPolicy:
    gate = body.specified_value("mastery_gate")
    entries = gate.get("adaptive_mastery_policies") if isinstance(gate, dict) else None
    try:
        matches = [
            MasteryPolicy.model_validate(entry)
            for entry in (entries if isinstance(entries, list) else ())
            if isinstance(entry, dict) and entry.get("policy_uuid") == mastery_uuid
        ]
    except ValidationError:
        matches = []
    if len(matches) != 1:
        raise studytomy_http_error(
            409,
            POLICY_NOT_APPROVED,
            "An approved compatible mastery policy is required.",
        )
    return matches[0]


@dataclass(frozen=True, slots=True)
class MasteryObservation:
    logical_source_uuid: str
    result_revision: int
    evidence_sequence: int
    item_family_uuid: str
    session_uuid: str
    session_started_at: datetime
    observed_at: datetime
    outcome: str
    independence: str
    eligible: bool
    is_retraction: bool = False


@dataclass(frozen=True, slots=True)
class MasteryResult:
    state: MasteryState
    eligible_count: int
    success_count: int
    assisted_count: int
    unknown_count: int
    score: Decimal | None
    latest_independent_at: datetime | None
    review_due: bool


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _spaced_sessions(
    evidence: tuple[MasteryObservation, ...], separation: timedelta
) -> int:
    starts = {item.session_uuid: _utc(item.session_started_at) for item in evidence}
    count = 0
    next_start = None
    for start in sorted(starts.values()):
        if next_start is None or start >= next_start:
            count += 1
            next_start = start + separation
    return count


def assess_mastery_window(
    policy: MasteryPolicy,
    evidence: tuple[MasteryObservation, ...],
    *,
    assisted_count: int,
    unknown_count: int,
    now: datetime,
) -> MasteryResult:
    """Evaluate a latest-first, eligible, distinct-family window."""
    n = len(evidence)
    if not n:
        return MasteryResult(
            "not_assessed", 0, 0, assisted_count, unknown_count, None, None, False
        )
    correct = sum(item.outcome == "correct" for item in evidence)
    if (
        n < policy.minimum_families
        or _spaced_sessions(
            evidence,
            timedelta(minutes=policy.separation_minutes),
        )
        < policy.minimum_sessions
    ):
        state = "insufficient_evidence"
    elif correct * 100 < policy.success_threshold_percent * n:
        state = "developing"
    else:
        state = "mastered"
    with localcontext() as context:
        context.prec = 28
        score = (Decimal(correct) / Decimal(n)).quantize(
            Decimal("0.000000001"), rounding=ROUND_HALF_EVEN
        )
    latest = _utc(evidence[0].observed_at)
    return MasteryResult(
        state,
        n,
        correct,
        assisted_count,
        unknown_count,
        score,
        latest,
        _utc(now) >= latest + timedelta(days=policy.review_after_days),
    )


def reduce_mastery(
    policy: MasteryPolicy,
    observations: Iterable[MasteryObservation],
    *,
    watermark: int,
    now: datetime,
) -> MasteryResult:
    """Reference reducer for one learner and concept at a captured journal watermark."""
    latest: dict[str, MasteryObservation] = {}
    for event in observations:
        if event.evidence_sequence > watermark:
            continue
        previous = latest.get(event.logical_source_uuid)
        if previous is None or (event.result_revision, event.evidence_sequence) > (
            previous.result_revision,
            previous.evidence_sequence,
        ):
            latest[event.logical_source_uuid] = event
    current = [event for event in latest.values() if not event.is_retraction]
    eligible = sorted(
        (
            event
            for event in current
            if event.eligible
            and event.independence == "independent"
            and event.outcome in {"correct", "incorrect"}
        ),
        key=lambda event: (_utc(event.observed_at), event.evidence_sequence),
        reverse=True,
    )
    distinct: dict[str, MasteryObservation] = {}
    for event in eligible:
        distinct.setdefault(event.item_family_uuid, event)
        if len(distinct) == policy.window_size:
            break
    return assess_mastery_window(
        policy,
        tuple(distinct.values()),
        assisted_count=sum(event.independence == "assisted" for event in current),
        unknown_count=sum(event.independence == "unknown" for event in current),
        now=now,
    )
