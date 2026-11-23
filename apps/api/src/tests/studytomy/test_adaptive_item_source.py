from decimal import Decimal

import pytest

from src.db.courses.assignments import AssignmentTask, AssignmentTaskTypeEnum
from src.services.studytomy.adaptive_item_source import (
    parse_item_decimal,
    snapshot_native_question,
)


def _quiz(*flags):
    return AssignmentTask(
        assignment_type=AssignmentTaskTypeEnum.QUIZ,
        contents={
            "questions": [
                {
                    "questionUUID": "question_a",
                    "questionText": "Which is equivalent to one half?",
                    "options": [
                        {
                            "optionUUID": f"option_{index}",
                            "text": value,
                            "assigned_right_answer": flag,
                        }
                        for index, (value, flag) in enumerate(
                            zip(("2/4", "3/4"), flags, strict=True)
                        )
                    ],
                }
            ]
        },
    )


def test_choice_snapshot_allowlists_public_fields_and_resolves_legacy_single_response():
    question, answer = snapshot_native_question(_quiz(True, False), "question_a")
    assert answer == {"correct_option_uuid": "option_0"}
    assert question.model_dump() == {
        "kind": "single_choice",
        "prompt": "Which is equivalent to one half?",
        "options": [
            {"option_uuid": "option_0", "text": "2/4"},
            {"option_uuid": "option_1", "text": "3/4"},
        ],
    }


@pytest.mark.parametrize("flags", [(True, True), (False, False), ("false", True)])
def test_choice_source_rejects_multiple_missing_or_malformed_keys(flags):
    with pytest.raises(ValueError):
        snapshot_native_question(_quiz(*flags), "question_a")


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1e2000", "1_000", "1kg", " "])
def test_number_representation_rejects_nonfinite_or_unsupported_input(value):
    with pytest.raises(ValueError):
        parse_item_decimal(value)


def test_number_representation_preserves_decimal_values():
    assert parse_item_decimal("1.725e1") == Decimal("17.25")
