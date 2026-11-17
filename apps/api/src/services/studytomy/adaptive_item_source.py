"""Load and project the exact native source of a candidate assessment item."""

import re
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import Activity, ActivityTypeEnum
from src.db.courses.assignments import (
    Assignment,
    AssignmentTask,
    AssignmentTaskTypeEnum,
    SolutionRevealEnum,
)
from src.db.courses.chapter_activities import ChapterActivity
from src.db.courses.chapters import Chapter
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.users import PublicUser
from src.services.courses.activities.quiz_modes import (
    RESPONSE_TYPE_SINGLE,
    resolve_response_type,
)
from src.services.studytomy.adaptive_access import authorize_adaptive_course_editor
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.idempotency import canonical_request_hash

SourceLocator = Annotated[str, StringConstraints(min_length=1, max_length=100)]
NativeSourceExposure = Literal["possible_native_reveal", "unverified_native_history"]
SourceExposure = Literal[
    "possible_native_reveal", "unverified_native_history", "private_original"
]
ItemKind = Literal["single_choice", "number"]
_DECIMAL_SYNTAX = re.compile(
    r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"
)


class PublicOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_uuid: SourceLocator
    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)
    ]


class ChoiceQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["single_choice"] = "single_choice"
    prompt: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2500)
    ]
    options: Annotated[list[PublicOption], Field(min_length=2, max_length=12)]


class NumberQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["number"] = "number"
    prompt: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2500)
    ]
    unit: Annotated[str, StringConstraints(strip_whitespace=True, max_length=80)] = ""


PublicQuestion = Annotated[ChoiceQuestion | NumberQuestion, Field(discriminator="kind")]


class ItemSourceRead(BaseModel):
    source_task_uuid: str | None
    source_question_uuid: str | None
    assignment_uuid: str | None
    course_uuid: str
    source_digest: str
    source_exposure: SourceExposure
    question: PublicQuestion


class NativeItemSourceRead(ItemSourceRead):
    source_task_uuid: str
    assignment_uuid: str
    source_exposure: NativeSourceExposure


def parse_item_decimal(value: str) -> Decimal:
    """Accept only bounded ASCII decimal notation for ALE numerical evidence."""
    if len(value) > 128 or _DECIMAL_SYNTAX.fullmatch(value) is None:
        raise ValueError("Expected a bounded finite decimal.")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError("Expected a bounded finite decimal.") from error
    if (
        not parsed.is_finite()
        or abs(parsed.as_tuple().exponent) > 100
        or abs(parsed.adjusted()) > 100
    ):
        raise ValueError("Expected a bounded finite decimal.")
    return parsed


def _native_decimal(raw: object) -> Decimal:
    if type(raw) not in {float, int, str}:
        raise ValueError("Invalid numeric source value.")
    return parse_item_decimal(str(raw))


def _choice_question(
    contents: dict, question_uuid: str | None
) -> tuple[ChoiceQuestion, dict]:
    questions = contents.get("questions")
    if question_uuid is None or not isinstance(questions, list):
        raise ValueError("A native quiz requires a question locator.")
    matches = [
        q
        for q in questions
        if isinstance(q, dict) and q.get("questionUUID") == question_uuid
    ]
    if len(matches) != 1:
        raise ValueError("The native question locator is missing or ambiguous.")
    question = matches[0]
    raw_options = question.get("options")
    if not isinstance(raw_options, list) or not 2 <= len(raw_options) <= 12:
        raise ValueError("Unsupported choice set.")
    options = []
    correct = []
    for option in raw_options:
        if (
            not isinstance(option, dict)
            or type(option.get("assigned_right_answer")) is not bool
            or option.get("type", "text") != "text"
            or option.get("fileID")
        ):
            raise ValueError("Only plain-text, explicitly keyed choices are supported.")
        public = PublicOption(
            option_uuid=option.get("optionUUID"), text=option.get("text")
        )
        options.append(public)
        if option["assigned_right_answer"]:
            correct.append(public.option_uuid)
    if (
        len({option.option_uuid for option in options}) != len(options)
        or len(correct) != 1
        or resolve_response_type(question) != RESPONSE_TYPE_SINGLE
    ):
        raise ValueError(
            "A single-choice item requires one unambiguous correct answer."
        )
    return ChoiceQuestion(prompt=question.get("questionText"), options=options), {
        "correct_option_uuid": correct[0],
    }


def snapshot_native_question(
    task: AssignmentTask, question_uuid: str | None
) -> tuple[PublicQuestion, dict]:
    contents = task.contents
    if not isinstance(contents, dict):
        raise TypeError("The native task has no supported question.")
    if task.assignment_type == AssignmentTaskTypeEnum.QUIZ:
        return _choice_question(contents, question_uuid)
    if (
        task.assignment_type == AssignmentTaskTypeEnum.NUMBER_ANSWER
        and question_uuid is None
    ):
        answer = _native_decimal(contents.get("correct_value"))
        tolerance = abs(_native_decimal(contents.get("tolerance", 0)))
        return NumberQuestion(
            prompt=contents.get("prompt"), unit=contents.get("unit") or ""
        ), {
            "expected": str(answer),
            "tolerance": str(tolerance),
        }
    raise ValueError("Unsupported native task kind or question locator.")


async def read_native_item_source(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    task_uuid: str,
    question_uuid: str | None,
    *,
    lock: bool = False,
) -> tuple[NativeItemSourceRead, dict]:
    task, assignment, course, activity = await _load_native_item_source(
        db,
        org,
        task_uuid,
        lock=lock,
    )
    await authorize_adaptive_course_editor(request, db, org, actor, course.course_uuid)
    return _project_native_item_source(
        task, assignment, course, activity, question_uuid
    )


async def _load_native_item_source(
    db: AsyncSession,
    org: Organization,
    task_uuid: str,
    *,
    lock: bool,
) -> tuple[AssignmentTask, Assignment, Course, Activity]:
    query = (
        select(AssignmentTask, Assignment, Course, Activity)
        .join(Assignment, AssignmentTask.assignment_id == Assignment.id)
        .join(Course, Assignment.course_id == Course.id)
        .join(Activity, Assignment.activity_id == Activity.id)
        .join(Chapter, Assignment.chapter_id == Chapter.id)
        .where(
            AssignmentTask.assignment_task_uuid == task_uuid,
            AssignmentTask.org_id == org.id,
            Assignment.org_id == org.id,
            Course.org_id == org.id,
            Activity.org_id == org.id,
            Chapter.org_id == org.id,
            Chapter.course_id == Course.id,
            Activity.activity_type == ActivityTypeEnum.TYPE_ASSIGNMENT,
            select(ChapterActivity.chapter_id)
            .where(
                ChapterActivity.chapter_id == Chapter.id,
                ChapterActivity.activity_id == Activity.id,
                ChapterActivity.org_id == org.id,
                ChapterActivity.course_id == Course.id,
            )
            .exists(),
            AssignmentTask.course_id == Course.id,
            Activity.course_id == Course.id,
            AssignmentTask.activity_id == Activity.id,
            AssignmentTask.chapter_id == Assignment.chapter_id,
        )
        .limit(2)
        .execution_options(populate_existing=True)
    )
    rows = (await db.execute(query.with_for_update(read=True) if lock else query)).all()
    if len(rows) != 1:
        raise HTTPException(404, "Assignment task not found.")
    return tuple(rows[0])


def _project_native_item_source(
    task: AssignmentTask,
    assignment: Assignment,
    course: Course,
    activity: Activity,
    question_uuid: str | None,
) -> tuple[NativeItemSourceRead, dict]:
    try:
        question, answer = snapshot_native_question(task, question_uuid)
    except (ValidationError, ValueError, TypeError) as error:
        raise studytomy_http_error(
            422,
            "ITEM_SOURCE_UNSUPPORTED",
            "This source does not contain an eligible single-choice or numeric question.",
        ) from error
    can_reveal = bool(assignment.show_correct_answers) or (
        bool(assignment.solution or assignment.solution_file)
        and assignment.solution_reveal not in {None, SolutionRevealEnum.NEVER}
    )
    exposure = "possible_native_reveal" if can_reveal else "unverified_native_history"
    digest = canonical_request_hash(
        {
            "schema": "adaptive_native_item_v1",
            "course": course.course_uuid,
            "activity": activity.activity_uuid,
            "assignment": assignment.assignment_uuid,
            "task": task.assignment_task_uuid,
            "question": question_uuid,
            "task_type": task.assignment_type,
            "contents": task.contents,
            "exposure": exposure,
        }
    )
    return NativeItemSourceRead(
        source_task_uuid=task.assignment_task_uuid,
        source_question_uuid=question_uuid,
        assignment_uuid=assignment.assignment_uuid,
        course_uuid=course.course_uuid,
        source_digest=digest,
        source_exposure=exposure,
        question=question,
    ), answer
