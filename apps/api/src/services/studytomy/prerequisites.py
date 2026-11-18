"""Explicit managed pass prerequisites in the approved D-09 policy."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from src.db.studytomy.governance import StudyPolicyBody
from src.services.studytomy.errors import studytomy_http_error

ResourceUUID = Annotated[str, Field(strict=True, min_length=1, max_length=100, pattern=r"^\S+$")]


class AssessedPassPrerequisite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["assessed_pass"]
    target_activity_uuid: ResourceUUID
    assessment_assignment_uuid: ResourceUUID


_PASS_RULES = TypeAdapter(
    dict[ResourceUUID, Annotated[list[AssessedPassPrerequisite], Field(max_length=500)]]
)


def _course_pass_rules(body: StudyPolicyBody, course_uuid: str) -> list[AssessedPassPrerequisite]:
    completion = body.specified_value("completion_prerequisites")
    if not isinstance(completion, dict) or "assessed_pass_by_course" not in completion:
        return []
    return _PASS_RULES.validate_python(completion["assessed_pass_by_course"]).get(course_uuid, [])


def ungated_by_assessed_pass(
    body: StudyPolicyBody, course_uuid: str, activity_uuid: str | None = None
) -> bool:
    """Exclude a target, or the whole offer, until release-bound grading is supported."""
    try:
        rules = _course_pass_rules(body, course_uuid)
    except ValidationError:
        return False
    return not any(
        activity_uuid is None or rule.target_activity_uuid == activity_uuid
        for rule in rules
    )


def validate_release_pass_prerequisites(body: StudyPolicyBody, snapshot: dict) -> None:
    """Require each prerequisite to point to an earlier graded activity in this release."""
    invalid = studytomy_http_error(
        409, "RELEASE_ASSESSED_PASS_INVALID",
        "Each assessed-pass prerequisite must reference a published, graded assignment earlier in this course.",
    )
    try:
        rules = _course_pass_rules(body, snapshot["course"]["course_uuid"])
    except ValidationError as error:
        raise invalid from error
    if not rules:
        return

    activities = {row["activity_uuid"]: row for row in snapshot["activities"]}
    activities_by_id = {row["id"]: row for row in activities.values()}
    assignments = {row["assignment_uuid"]: row for row in snapshot["assignments"]}
    chapter_orders = dict(snapshot["chapter_order"])
    if len(set(chapter_orders.values())) != len(chapter_orders):
        raise invalid
    positions = {}
    for chapter_id, activity_id, order in snapshot["activity_order"]:
        if chapter_id not in chapter_orders or activity_id in positions:
            raise invalid
        positions[activity_id] = (chapter_orders[chapter_id], order)

    seen = set()
    for rule in rules:
        pair = (rule.target_activity_uuid, rule.assessment_assignment_uuid)
        target = activities.get(rule.target_activity_uuid)
        assignment = assignments.get(rule.assessment_assignment_uuid)
        if pair in seen or not target or target.get("published") is not True or not assignment:
            raise invalid
        seen.add(pair)
        source_id = assignment.get("activity_id")
        source = activities_by_id.get(source_id)
        if (
            assignment.get("published") is not True
            or assignment.get("ungraded") is True
            or not source
            or source.get("published") is not True
            or source.get("activity_sub_type") != "SUBTYPE_ASSIGNMENT_ANY"
            or source_id not in positions
            or target["id"] not in positions
            or positions[source_id] >= positions[target["id"]]
        ):
            raise invalid
