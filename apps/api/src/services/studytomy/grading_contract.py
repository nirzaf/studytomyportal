"""Shared fingerprint for live and retained native assignment grading inputs."""

from collections.abc import Iterable

from src.db.courses.assignments import AssignmentTaskTypeEnum, GradingTypeEnum
from src.services.studytomy.idempotency import canonical_request_hash

NATIVE_GRADING_CONTRACT_VERSION = "native-assessment-v1"


def native_grading_digest(
    assignment_uuid: str,
    grading_type: GradingTypeEnum,
    pass_threshold_percentage: float | None,
    ungraded: bool,
    tasks: Iterable[tuple[str, AssignmentTaskTypeEnum, int, dict]],
) -> str:
    return canonical_request_hash({
        "version": NATIVE_GRADING_CONTRACT_VERSION,
        "assignment": assignment_uuid,
        "grading_type": grading_type,
        "pass_threshold_percentage": pass_threshold_percentage,
        "ungraded": ungraded,
        "tasks": sorted(tasks, key=lambda task: task[0]),
    })


def retained_grading_contract_digest(assignment: dict, tasks: list[dict]) -> str:
    """Recover the native contract from exact snapshot rows, rejecting vacuous passes."""
    threshold = assignment["pass_threshold_percentage"]
    if threshold is not None and (
        type(threshold) not in (int, float) or not 0 <= threshold <= 100
    ):
        raise ValueError("The retained passing threshold is invalid.")
    rows = []
    for task in tasks:
        if task["assignment_id"] != assignment["id"]:
            continue
        points = task["max_grade_value"]
        if type(points) is not int or points < 0 or not isinstance(task["contents"], dict):
            raise ValueError("The retained grading task is invalid.")
        rows.append((
            task["assignment_task_uuid"], AssignmentTaskTypeEnum(task["assignment_type"]),
            points, task["contents"],
        ))
    if len({row[0] for row in rows}) != len(rows) or sum(row[2] for row in rows) <= 0:
        raise ValueError("The retained assignment needs unique tasks with a positive point total.")
    return native_grading_digest(
        assignment["assignment_uuid"], GradingTypeEnum(assignment["grading_type"]),
        threshold, bool(assignment["ungraded"]), rows,
    )
