import pytest
from fastapi import HTTPException

from src.services.ai.learning_actions import evaluate_fraction
from src.services.ai.learning_practice import FractionEvaluationBasis


@pytest.mark.parametrize(("answer", "expected"), [("3/4", True), ("6 / 8", True), ("2/3", False), ("1", False)])
def test_practice_uses_exact_rational_equality(answer, expected):
    basis = FractionEvaluationBasis(template="equivalent_fractions_v1", numerator=3, denominator=4)
    assert evaluate_fraction(answer, basis) is expected


@pytest.mark.parametrize("answer", ["1/0", "1/-2", "1.0", "two", "10001/10000", "1/10001", "1/2/3"])
def test_practice_rejects_malformed_or_out_of_range_rationals(answer):
    basis = FractionEvaluationBasis(template="equivalent_fractions_v1", numerator=1, denominator=2)
    with pytest.raises(HTTPException) as invalid:
        evaluate_fraction(answer, basis)
    assert invalid.value.status_code == 422
    assert invalid.value.detail["code"] == "PRACTICE_ANSWER_INVALID"
