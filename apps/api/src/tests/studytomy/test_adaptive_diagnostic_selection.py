from src.services.studytomy.adaptive_diagnostic_selection import (
    DiagnosticLimits,
    DiagnosticManifest,
    DiagnosticObservation,
    DiagnosticPoolItem,
    DiagnosticRules,
    DiagnosticSelection,
    select_diagnostic_item,
)


def _manifest(*, minimum=2, cap=6):
    return DiagnosticManifest(
        required_concepts=("a", "b"),
        prerequisites={"a": ("b",), "b": ()},
        pool=tuple(
            DiagnosticPoolItem(
                uuid=uuid,
                concept_uuid=concept,
                family_uuid=f"family-{uuid}",
                difficulty=difficulty,
                content_digest="a" * 64,
            )
            for uuid, concept, difficulty in (
                ("a-baseline", "a", 2),
                ("a-hard", "a", 3),
                ("a-easy", "a", 1),
                ("b-baseline", "b", 2),
                ("b-probe", "b", 3),
            )
        ),
        limits=DiagnosticLimits(
            total_items=cap,
            items_per_concept=3,
            minimum_scored_per_concept=minimum,
            expires_after_minutes=60,
        ),
        rules=DiagnosticRules(
            allow_help=False, reveal="never", max_attempts=2, retake_wait_minutes=0
        ),
        mastery_policy_digest="a" * 64,
    )


def test_probe_starts_only_after_baseline_coverage_and_replays_identically():
    manifest = _manifest()
    first = select_diagnostic_item(manifest, (), frozenset())
    assert first.item_uuid == "a-baseline"
    history = (DiagnosticObservation(first.item_uuid, "correct"),)
    assert (
        select_diagnostic_item(manifest, history, frozenset()).item_uuid == "b-baseline"
    )
    history += (DiagnosticObservation("b-baseline", "correct"),)
    reopened = DiagnosticManifest.model_validate_json(manifest.model_dump_json())
    assert select_diagnostic_item(
        reopened, history, frozenset()
    ) == DiagnosticSelection("a-hard")
    assert select_diagnostic_item(
        manifest, history, frozenset()
    ) == DiagnosticSelection("a-hard")


def test_wrong_baseline_probes_reviewed_prerequisite_and_exclusion_falls_back_to_easier_item():
    history = (
        DiagnosticObservation("a-baseline", "incorrect"),
        DiagnosticObservation("b-baseline", "correct"),
    )
    assert (
        select_diagnostic_item(_manifest(), history, frozenset()).item_uuid == "b-probe"
    )
    result = select_diagnostic_item(_manifest(), history, frozenset({"family-b-probe"}))
    assert result.item_uuid == "a-easy"


def test_exposed_families_and_skips_never_inflate_coverage():
    history = (
        DiagnosticObservation("a-baseline", "skip"),
        DiagnosticObservation("b-baseline", "correct"),
    )
    exposed = frozenset({"family-a-hard", "family-a-easy", "family-b-probe"})
    decision = select_diagnostic_item(_manifest(minimum=1), history, exposed)
    assert decision == DiagnosticSelection(None, "insufficient_items")


def test_repeated_family_cannot_turn_a_skip_into_scored_coverage():
    history = (
        DiagnosticObservation("a-baseline", "skip"),
        DiagnosticObservation("a-baseline", "correct"),
        DiagnosticObservation("b-baseline", "correct"),
    )
    decision = select_diagnostic_item(
        _manifest(minimum=1),
        history,
        frozenset({"family-a-hard", "family-a-easy"}),
    )
    assert decision.stop_reason == "insufficient_items"


def test_selector_distinguishes_success_from_cap_and_pool_exhaustion():
    history = (
        DiagnosticObservation("a-baseline", "correct"),
        DiagnosticObservation("b-baseline", "correct"),
    )
    assert (
        select_diagnostic_item(
            _manifest(minimum=1, cap=2), history, frozenset()
        ).stop_reason
        == "coverage_met"
    )
    skipped = (
        DiagnosticObservation("a-baseline", "skip"),
        DiagnosticObservation("b-baseline", "correct"),
    )
    assert (
        select_diagnostic_item(
            _manifest(minimum=1, cap=2), skipped, frozenset()
        ).stop_reason
        == "item_limit"
    )
    exposed = frozenset(item.family_uuid for item in _manifest().pool)
    assert (
        select_diagnostic_item(_manifest(), (), exposed).stop_reason
        == "insufficient_items"
    )
