"""Pure server-side evaluation of an issued, reviewed objective item."""

from dataclasses import dataclass
from decimal import localcontext
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
)

from src.db.studytomy.adaptive_items import StudyItemRevision
from src.services.studytomy.adaptive_item_source import (
    ChoiceQuestion,
    PublicQuestion,
    SourceLocator,
    parse_item_decimal,
)

OBJECTIVE_EVALUATOR = "adaptive_objective_v1"


class ChoiceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["single_choice"]
    option_uuid: SourceLocator


class NumberResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["number"]
    value: Annotated[str, StringConstraints(min_length=1, max_length=128)]


ObjectiveResponse = Annotated[
    ChoiceResponse | NumberResponse, Field(discriminator="kind")
]
_QUESTIONS = TypeAdapter(PublicQuestion)
_RESPONSES = TypeAdapter(ObjectiveResponse)


@dataclass(frozen=True, slots=True)
class ObjectiveResult:
    outcome: Literal["correct", "incorrect", "not_scored"]
    normalized_response: str | None = None
    error_code: str | None = None


class InvalidObjectiveResponse(ValueError):
    """The caller can return 422 without accepting a response or granting a score."""


def _parse_response(payload: object) -> ObjectiveResponse:
    try:
        return _RESPONSES.validate_python(payload)
    except ValidationError as error:
        raise InvalidObjectiveResponse(
            "Provide one response using the issued question's format."
        ) from error


def evaluate_objective_item(
    item: StudyItemRevision, payload: object
) -> ObjectiveResult:
    """Grade an issued snapshot without modifying evidence, mastery or the native gradebook."""
    if item.evaluator_version != OBJECTIVE_EVALUATOR:
        return ObjectiveResult("not_scored")
    try:
        question = _QUESTIONS.validate_python(item.public_question)
    except ValidationError:
        return ObjectiveResult("not_scored")
    rubric = item.private_answer
    if (
        question.kind != item.kind
        or not isinstance(rubric, dict)
        or not isinstance(item.error_codes, dict)
    ):
        return ObjectiveResult("not_scored")
    if isinstance(question, ChoiceQuestion):
        return _score_choice(question, rubric, item.error_codes, payload)
    return _score_number(rubric, item.error_codes, payload)


def _score_choice(
    question: ChoiceQuestion,
    rubric: dict,
    error_codes: dict[str, str],
    payload: object,
) -> ObjectiveResult:
    options = {option.option_uuid for option in question.options}
    correct = rubric.get("correct_option_uuid")
    if (
        not isinstance(correct, str)
        or correct not in options
        or len(options) != len(question.options)
    ):
        return ObjectiveResult("not_scored")
    response = _parse_response(payload)
    if not isinstance(response, ChoiceResponse) or response.option_uuid not in options:
        raise InvalidObjectiveResponse("Choose one of the issued options.")
    if response.option_uuid == correct:
        return ObjectiveResult("correct", response.option_uuid)
    return ObjectiveResult(
        "incorrect", response.option_uuid, error_codes.get(response.option_uuid)
    )


def _score_number(
    rubric: dict,
    error_codes: dict[str, str],
    payload: object,
) -> ObjectiveResult:
    expected_value = rubric.get("expected")
    tolerance_value = rubric.get("tolerance")
    if not isinstance(expected_value, str) or not isinstance(tolerance_value, str):
        return ObjectiveResult("not_scored")
    try:
        expected = parse_item_decimal(expected_value)
        tolerance = parse_item_decimal(tolerance_value)
    except ValueError:
        return ObjectiveResult("not_scored")
    if tolerance < 0:
        return ObjectiveResult("not_scored")
    response = _parse_response(payload)
    if not isinstance(response, NumberResponse):
        raise InvalidObjectiveResponse("Provide one bounded decimal value.")
    try:
        value = parse_item_decimal(response.value)
    except ValueError as error:
        raise InvalidObjectiveResponse("Provide one bounded decimal value.") from error
    # The source parser bounds exponents and magnitudes to 100. Allow enough
    # precision for exact subtraction across that range, independent of caller context.
    with localcontext() as ctx:
        ctx.prec = 256
        correct = abs(value - expected) <= tolerance
        normalized = "0" if not value else str(value.normalize())
    if len(normalized) > 128:
        normalized = response.value
    if correct:
        return ObjectiveResult("correct", normalized)
    return ObjectiveResult("incorrect", normalized, error_codes.get("incorrect"))
