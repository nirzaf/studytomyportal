from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from src.services.studytomy.adaptive_mastery import (
    MasteryObservation,
    MasteryPolicy,
    reduce_mastery,
)

START = datetime(2025, 1, 1, 12, tzinfo=UTC)
NOW = START + timedelta(days=2)
POLICY = MasteryPolicy(
    policy_uuid="fixture",
    reducer_version="adaptive_mastery_v1",
    window_size=3,
    minimum_families=2,
    success_threshold_percent=75,
    minimum_sessions=2,
    separation_minutes=1440,
    review_after_days=10,
)


def _event(sequence, *, family=None, session=1, hours=0, **changes):
    values = {
        "logical_source_uuid": f"observation_{sequence}",
        "result_revision": 1,
        "evidence_sequence": sequence,
        "item_family_uuid": family or f"family_{sequence}",
        "session_uuid": f"session_{session}",
        "session_started_at": START + timedelta(hours=hours),
        "observed_at": START + timedelta(hours=hours, minutes=1),
        "outcome": "correct",
        "independence": "independent",
        "eligible": True,
    }
    return MasteryObservation(**(values | changes))


def test_absent_unscored_and_measured_zero_are_distinct():
    assert reduce_mastery(POLICY, [], watermark=0, now=NOW).score is None
    skip = _event(1, outcome="not_scored", independence="unknown", eligible=False)
    only_skip = reduce_mastery(POLICY, [skip], watermark=1, now=NOW)
    assert only_skip.state == "not_assessed" and only_skip.unknown_count == 1
    measured = reduce_mastery(
        POLICY,
        [
            _event(1, outcome="incorrect"),
            _event(2, outcome="incorrect", session=2, hours=24),
        ],
        watermark=2,
        now=NOW,
    )
    assert measured.state == "developing" and measured.score == Decimal(0)


def test_same_session_or_unspaced_sessions_cannot_show_mastery():
    first = _event(1)
    assert (
        reduce_mastery(
            POLICY,
            [first, _event(2)],
            watermark=2,
            now=NOW,
        ).state
        == "insufficient_evidence"
    )
    assert (
        reduce_mastery(
            POLICY,
            [first, _event(2, session=2, hours=23)],
            watermark=2,
            now=NOW,
        ).state
        == "insufficient_evidence"
    )
    assert (
        reduce_mastery(
            POLICY,
            [first, _event(2, session=2, hours=24)],
            watermark=2,
            now=NOW,
        ).state
        == "mastered"
    )


def test_family_dedup_and_assistance_do_not_inflate_scored_coverage():
    rows = [
        _event(1, family="shared"),
        _event(2, family="shared", session=2, hours=24, outcome="incorrect"),
        _event(3, session=3, hours=48, independence="assisted", eligible=False),
        _event(4, session=3, hours=48, independence="unknown", eligible=False),
    ]
    result = reduce_mastery(POLICY, rows, watermark=4, now=NOW)
    assert result.state == "insufficient_evidence"
    assert result.eligible_count == 1 and result.success_count == 0
    assert result.assisted_count == result.unknown_count == 1


def test_correction_retraction_and_policy_replay_hold_the_watermark():
    first = _event(1)
    second = _event(2, session=2, hours=24)
    correction = replace(
        first, evidence_sequence=3, result_revision=2, outcome="incorrect"
    )
    retraction = replace(
        second,
        evidence_sequence=4,
        result_revision=2,
        outcome="not_scored",
        eligible=False,
        is_retraction=True,
    )
    rows = [correction, retraction, second, first]
    assert reduce_mastery(POLICY, rows, watermark=2, now=NOW).state == "mastered"
    assert reduce_mastery(POLICY, rows, watermark=3, now=NOW).state == "developing"
    assert (
        reduce_mastery(POLICY, rows, watermark=4, now=NOW).state
        == "insufficient_evidence"
    )
    lower = POLICY.model_copy(update={"success_threshold_percent": 50})
    assert lower.digest != POLICY.digest
    assert reduce_mastery(lower, rows, watermark=3, now=NOW).state == "mastered"
    assert reduce_mastery(
        lower, reversed(rows), watermark=3, now=NOW
    ) == reduce_mastery(
        lower,
        rows,
        watermark=3,
        now=NOW,
    )


def test_freshness_changes_without_score_decay():
    rows = [_event(1), _event(2, session=2, hours=24)]
    early = reduce_mastery(POLICY, rows, watermark=2, now=NOW)
    late = reduce_mastery(POLICY, rows, watermark=2, now=START + timedelta(days=12))
    assert early == replace(late, review_due=False)
    assert late.review_due and late.score == Decimal(1)
