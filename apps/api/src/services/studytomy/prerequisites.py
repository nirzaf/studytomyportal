"""Explicit managed pass prerequisites in the approved D-09 policy."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from src.db.studytomy.governance import StudyPolicyBody

ResourceUUID = Annotated[str, Field(strict=True, min_length=1, max_length=100, pattern=r"^\S+$")]


class AssessedPassPrerequisite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["assessed_pass"]
    target_activity_uuid: ResourceUUID
    assessment_assignment_uuid: ResourceUUID


_PASS_RULES = TypeAdapter(
    dict[ResourceUUID, Annotated[list[AssessedPassPrerequisite], Field(max_length=500)]]
)


def ungated_by_assessed_pass(
    body: StudyPolicyBody, course_uuid: str, activity_uuid: str | None = None
) -> bool:
    """Exclude a target, or the whole offer, until release-bound grading is supported."""
    completion = body.specified_value("completion_prerequisites")
    if not isinstance(completion, dict) or "assessed_pass_by_course" not in completion:
        return True
    try:
        rules = _PASS_RULES.validate_python(completion["assessed_pass_by_course"])
    except ValidationError:
        return False
    return not any(
        activity_uuid is None or rule.target_activity_uuid == activity_uuid
        for rule in rules.get(course_uuid, [])
    )
