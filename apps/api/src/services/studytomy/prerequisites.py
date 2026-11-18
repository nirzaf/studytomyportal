"""Explicit managed pass prerequisites in the approved D-09 policy."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.assignments import Assignment, AssignmentUserSubmission, AssignmentUserSubmissionStatus
from src.db.studytomy.education import StudyCourseRelease
from src.db.studytomy.governance import StudyBusinessEvent, StudyPolicyBody
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.grading_contract import retained_grading_contract_digest

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


async def managed_assessed_pass_status(
    db: AsyncSession,
    body: StudyPolicyBody,
    release: StudyCourseRelease,
    user_id: int,
    activity_uuid: str,
) -> Literal["assessment_pass_required", "assessment_pass_delivery_pending"] | None:
    """Project evidence status for an already authorized release without opening delivery."""
    from src.services.studytomy.assessment_reviews import _appeal_receipt
    from src.services.studytomy.grading import (
        CONTRACT_FROZEN_ACTION,
        NATIVE_GRADING_CONTRACT_VERSION,
        latest_assessment_result,
    )

    try:
        sources = {
            rule.assessment_assignment_uuid
            for rule in _course_pass_rules(body, release.course_uuid)
            if rule.target_activity_uuid == activity_uuid
        }
    except ValidationError:
        return "assessment_pass_required"
    if not sources:
        return None
    for source in sorted(sources):
        retained = [
            row for row in release.snapshot.get("assignments", [])
            if row.get("assignment_uuid") == source
        ]
        try:
            if len(retained) != 1 or retained[0].get("published") is not True:
                return "assessment_pass_required"
            digest = retained_grading_contract_digest(retained[0], release.snapshot["assignment_tasks"])
        except (KeyError, TypeError, ValueError):
            return "assessment_pass_required"
        row = (await db.execute(
            select(Assignment, AssignmentUserSubmission)
            .join(AssignmentUserSubmission, AssignmentUserSubmission.assignment_id == Assignment.id)
            .where(
                Assignment.org_id == release.org_id,
                Assignment.course_id == release.snapshot["course"]["id"],
                Assignment.id == retained[0]["id"],
                Assignment.assignment_uuid == source,
                AssignmentUserSubmission.user_id == user_id,
                AssignmentUserSubmission.submission_status == AssignmentUserSubmissionStatus.GRADED,
            )
        )).one_or_none()
        if row is None:
            return "assessment_pass_required"
        result = await latest_assessment_result(db, *row)
        state = result.after_state if result is not None else None
        if (
            not isinstance(state, dict)
            or state.get("passed") is not True
            or state.get("source_snapshot_digest") != digest
            or state.get("evaluator_version") != NATIVE_GRADING_CONTRACT_VERSION
            or not state.get("grading_contract_event_uuid")
        ):
            return "assessment_pass_required"
        contract = await db.scalar(select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == release.org_id,
            StudyBusinessEvent.event_uuid == state["grading_contract_event_uuid"],
            StudyBusinessEvent.action == CONTRACT_FROZEN_ACTION,
            StudyBusinessEvent.resource_kind == "assignment_round",
            StudyBusinessEvent.resource_uuid == result.resource_uuid,
            StudyBusinessEvent.actor_user_id == user_id,
            StudyBusinessEvent.reason == "ASSESSMENT_ISSUED",
        ))
        if contract is None or contract.after_state != {
            "source_snapshot_digest": digest,
            "evaluator_version": NATIVE_GRADING_CONTRACT_VERSION,
            "assessment_release_uuid": release.uuid,
            "assessment_release_sha256": release.snapshot_sha256,
        }:
            return "assessment_pass_required"
        appeals = (await db.execute(select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == release.org_id,
            StudyBusinessEvent.actor_user_id == user_id,
            StudyBusinessEvent.action == "studytomy.assessment.appeal_requested",
            StudyBusinessEvent.operation_uuid == result.resource_uuid,
        ))).scalars()
        for appeal in appeals:
            if (await _appeal_receipt(db, appeal)).status not in {"result_confirmed", "result_corrected"}:
                return "assessment_pass_required"
    return "assessment_pass_delivery_pending"


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
        try:
            retained_grading_contract_digest(assignment, snapshot["assignment_tasks"])
        except (KeyError, TypeError, ValueError) as error:
            raise invalid from error
