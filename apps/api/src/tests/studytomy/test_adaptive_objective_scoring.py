from decimal import localcontext

import pytest

from src.db.studytomy.adaptive_items import StudyItemRevision
from src.services.courses.activities.assignments import _check_number_answer
from src.services.courses.activities.quiz_modes import (
    GRADING_MODE_ALL_OR_NOTHING,
    RESPONSE_TYPE_SINGLE,
    score_question,
)
from src.services.studytomy.adaptive_objective_scoring import (
    OBJECTIVE_EVALUATOR,
    InvalidObjectiveResponse,
    evaluate_objective_item,
)


def _choice():
    return StudyItemRevision(
        kind="single_choice",
        evaluator_version=OBJECTIVE_EVALUATOR,
        public_question={
            "kind": "single_choice",
            "prompt": "Choose a fraction.",
            "options": [
                {"option_uuid": "a", "text": "1/2"},
                {"option_uuid": "b", "text": "2/3"},
            ],
        },
        private_answer={"correct_option_uuid": "a"},
        error_codes={"b": "wrong_denominator"},
    )


def _number(expected="17.25", tolerance="0.125"):
    return StudyItemRevision(
        kind="number",
        evaluator_version=OBJECTIVE_EVALUATOR,
        public_question={
            "kind": "number",
            "prompt": "Measure the distance.",
            "unit": "cm",
        },
        private_answer={"expected": expected, "tolerance": tolerance},
        error_codes={"incorrect": "review_measurement"},
    )


@pytest.mark.parametrize("option", ["a", "b"])
def test_single_choice_matches_native_single_response_scoring(option):
    result = evaluate_objective_item(
        _choice(), {"kind": "single_choice", "option_uuid": option}
    )
    native = score_question(
        [(True, option == "a"), (False, option == "b")],
        RESPONSE_TYPE_SINGLE,
        GRADING_MODE_ALL_OR_NOTHING,
    )
    assert result.outcome == ("correct" if native == 1 else "incorrect")
    assert result.error_code == (None if native == 1 else "wrong_denominator")


@pytest.mark.parametrize("value", ["17.25", "17.375", "17.126", "17.125", "17.124"])
def test_numeric_matches_native_for_exactly_representable_rubrics(value):
    result = evaluate_objective_item(_number(), {"kind": "number", "value": value})
    native = _check_number_answer(value, "17.25", "0.125")
    assert result.outcome == ("correct" if native else "incorrect")


def test_decimal_boundary_is_exact_and_uses_independent_precision():
    with localcontext() as ctx:
        ctx.prec = 1
        item = _number("0.3", "0.1")
        boundary = evaluate_objective_item(item, {"kind": "number", "value": ".4000"})
        beyond = evaluate_objective_item(
            item, {"kind": "number", "value": "0.4000000000000000001"}
        )
    assert boundary.outcome == "correct" and boundary.normalized_response == "0.4"
    assert beyond.outcome == "incorrect" and beyond.error_code == "review_measurement"


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "single_choice", "option_uuid": "unknown"},
        {"kind": "single_choice", "option_uuid": ["a", "b"]},
        {"kind": "single_choice", "option_uuid": "a", "grade": "correct"},
        {"kind": "number", "value": "1"},
    ],
)
def test_choice_malformed_or_unissued_response_has_no_score(payload):
    with pytest.raises(InvalidObjectiveResponse):
        evaluate_objective_item(_choice(), payload)


@pytest.mark.parametrize("value", ["NaN", "inf", "1cm", "1e1000", "9" * 129, 17.25])
def test_number_malformed_answer_has_no_score(value):
    with pytest.raises(InvalidObjectiveResponse):
        evaluate_objective_item(_number(), {"kind": "number", "value": value})


def test_missing_or_unsupported_rubrics_never_create_incorrect_evidence():
    item = _number(tolerance="-0.1")
    assert (
        evaluate_objective_item(item, {"kind": "number", "value": "17.25"}).outcome
        == "not_scored"
    )
    item = _choice()
    item.private_answer = {}
    assert (
        evaluate_objective_item(
            item, {"kind": "single_choice", "option_uuid": "a"}
        ).outcome
        == "not_scored"
    )
    item.evaluator_version = "unknown"
    assert evaluate_objective_item(item, {}).outcome == "not_scored"
