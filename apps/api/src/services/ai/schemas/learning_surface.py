"""Version 1 of the native lesson AI interaction contract.

Model output selects from a catalogue. The application constructs the public
surface, including all identities, question instances, and available actions.
Evaluation data belongs in a separate server-only record.
"""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Revision = Annotated[int, Field(ge=1, le=2_147_483_647)]
WireUUID = Annotated[UUID, Field(strict=False)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,47}$")]
ContentIdentity = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
# Render these fields as React text nodes. The model proposal has no text fields.
PlainText = Annotated[str, StringConstraints(min_length=1, max_length=600, pattern=r"^[^<>]+$")]
PartCount = Annotated[int, Field(ge=1, le=12)]
Mark = Annotated[int, Field(ge=-12, le=12)]


class LessonTutorAction(StrEnum):
    """Action vocabulary for the launch tutor, separate from practice surface actions."""

    ASK_QUESTION = "ask_question"
    EXPLAIN_VISUAL = "explain_visual"
    DEEPEN_TOPIC = "deepen_topic"
    EVALUATE_ANSWER = "evaluate_answer"


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class LearningPresentation(_Contract):
    """The complete model-facing output schema for initial presentation selection."""

    visual: Literal["fraction_explorer", "number_line"]


class StartLearningSurface(_Contract):
    schema_version: Annotated[int, Field(ge=1, le=1)]
    request_id: WireUUID
    activity_uuid: Annotated[str, StringConstraints(pattern=r"^activity_[a-zA-Z0-9_-]{1,80}$")]
    mode: Literal["explore", "practice"]


class Explanation(_Contract):
    kind: Literal["explanation"]
    id: Identifier
    text: PlainText


class FractionExplorer(_Contract):
    kind: Literal["fraction_explorer"]
    id: Identifier
    parts: PartCount
    selected: Annotated[int, Field(ge=0, le=12)]

    @model_validator(mode="after")
    def check_selected_parts(self):
        if self.selected > self.parts:
            raise ValueError("Selected parts exceed the whole")
        return self


class NumberLine(_Contract):
    kind: Literal["number_line"]
    id: Identifier
    first_mark: Mark
    last_mark: Mark
    divisions_per_unit: PartCount

    @model_validator(mode="after")
    def check_mark_order(self):
        if self.last_mark <= self.first_mark:
            raise ValueError("Number line marks must be in increasing order")
        if (self.last_mark - self.first_mark) * self.divisions_per_unit > 48:
            raise ValueError("Number line has too many divisions")
        return self


class Choice(_Contract):
    id: Identifier
    label: PlainText


class RationalQuestion(_Contract):
    kind: Literal["rational"]
    id: WireUUID
    prompt: PlainText


class ChoiceQuestion(_Contract):
    kind: Literal["single_choice"]
    id: WireUUID
    prompt: PlainText
    choices: Annotated[list[Choice], Field(min_length=2, max_length=4)]

    @model_validator(mode="after")
    def check_choice_ids(self):
        if len({choice.id for choice in self.choices}) != len(self.choices):
            raise ValueError("Choice IDs must be unique")
        return self


Question = Annotated[RationalQuestion | ChoiceQuestion, Field(discriminator="kind")]


class QuestionCard(_Contract):
    kind: Literal["question"]
    id: Identifier
    question: Question


class Hint(_Contract):
    kind: Literal["hint"]
    id: Identifier
    text: PlainText


class Feedback(_Contract):
    kind: Literal["feedback"]
    id: Identifier
    outcome: Literal["correct", "incorrect"]
    attempt_id: WireUUID
    text: PlainText


Component = Annotated[
    Explanation | FractionExplorer | NumberLine | QuestionCard | Hint | Feedback,
    Field(discriminator="kind"),
]
Action = Literal["submit_answer", "request_hint", "retry_practice", "next_question"]


class LearningSurface(_Contract):
    schema_version: Annotated[int, Field(ge=1, le=1)]
    surface_id: WireUUID
    turn_id: WireUUID
    request_id: WireUUID
    revision: Revision
    activity_id: Annotated[str, StringConstraints(pattern=r"^activity_[a-zA-Z0-9_-]{1,80}$")]
    source_identity: ContentIdentity
    mode: Literal["explore", "practice"]
    components: Annotated[list[Component], Field(min_length=1, max_length=6)]
    actions: Annotated[list[Action], Field(max_length=4)]

    @model_validator(mode="after")
    def check_layout(self):
        kinds = {component.kind for component in self.components}
        if len(kinds) != len(self.components) or (
            len({component.id for component in self.components}) != len(self.components)
        ):
            raise ValueError("Component IDs and types must be unique")
        if len(kinds & {"fraction_explorer", "number_line"}) != 1:
            raise ValueError("The surface requires exactly one visual")
        if "explanation" not in kinds:
            raise ValueError("The surface requires an explanation")
        if len(set(self.actions)) != len(self.actions):
            raise ValueError("Actions must be unique")
        if self.mode == "explore":
            if self.actions or kinds & {"question", "hint", "feedback"}:
                raise ValueError("Explore surfaces cannot contain practice actions or evidence")
        elif "question" not in kinds:
            raise ValueError("Practice surfaces require a question")
        elif "feedback" in kinds and set(self.actions) & {"submit_answer", "request_hint"}:
            raise ValueError("Completed questions cannot be answered or request hints")
        elif "feedback" not in kinds and set(self.actions) & {"retry_practice", "next_question"}:
            raise ValueError("Retry and next-question actions require feedback")
        return self


class LearningAvailability(_Contract):
    available: bool
    surface: LearningSurface | None = None
    allow_explore: bool = False
    allow_practice: bool = False


class _ActionRequest(_Contract):
    surface_id: WireUUID
    request_id: WireUUID
    expected_revision: Revision


class SubmitAnswer(_ActionRequest):
    action: Literal["submit_answer"]
    answer: Annotated[str, StringConstraints(min_length=1, max_length=48)]


class PracticeAction(_ActionRequest):
    action: Literal["request_hint", "retry_practice", "next_question"]


ActionRequest = Annotated[SubmitAnswer | PracticeAction, Field(discriminator="action")]
