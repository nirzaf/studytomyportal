"""Evaluate and record ungraded lesson practice in a single transaction."""

import re
from fractions import Fraction
from uuid import uuid4

from fastapi import Request
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.studytomy.practice import StudyLearningAction
from src.db.users import PublicUser
from src.services.ai.learning_practice import (
    FractionEvaluationBasis,
    _authorize_source,
    ReviewedNumberLineExample,
    _latest_action,
    _owned_surface,
    _project_surface,
)
from src.services.ai.schemas.learning_surface import (
    ActionRequest,
    LearningSurface,
    SubmitAnswer,
)
from src.services.studytomy.errors import (
    IDEMPOTENCY_CONFLICT,
    STALE_VERSION,
    studytomy_http_error,
)
from src.services.studytomy.idempotency import canonical_request_hash

_ANSWER = re.compile(r"([+-]?[0-9]{1,5})(?: */ *([0-9]{1,5}))?")
def evaluate_fraction(answer: str, basis: FractionEvaluationBasis) -> bool:
    match = _ANSWER.fullmatch(answer.strip())
    if match is None:
        raise _invalid_answer()
    numerator = int(match[1])
    denominator = int(match[2] or 1)
    if abs(numerator) > 10_000 or not 1 <= denominator <= 10_000:
        raise _invalid_answer()
    return Fraction(numerator, denominator) == Fraction(basis.numerator, basis.denominator)


def _invalid_answer():
    return studytomy_http_error(
        422, "PRACTICE_ANSWER_INVALID",
        "Enter a whole number or a fraction with a positive denominator. Use numbers up to 10,000.",
    )


def _next_question(
    surface: LearningSurface,
    basis: FractionEvaluationBasis,
    source,
    *,
    retry: bool,
) -> tuple[list[dict], FractionEvaluationBasis | None, bool]:
    """Retry the same reviewed example or advance to the next approved example."""
    examples = list(source.approved_examples)
    index = next((i for i, item in enumerate(examples) if item.example_id == basis.reviewed_example_id), None)
    if index is None:
        raise studytomy_http_error(409, STALE_VERSION, "The reviewed practice example is no longer approved.")
    next_index = index if retry else index + 1
    if next_index >= len(examples):
        return [
            item.model_dump()
            for item in surface.components
            if item.kind not in {"hint", "feedback"}
        ], None, False

    example = examples[next_index]
    if example.visual == "fraction_explorer":
        visual = {
            "kind": example.visual, "id": "visual",
            "parts": example.parts, "selected": example.selected,
        }
        template = "equivalent_fractions_v1"
    elif isinstance(example, ReviewedNumberLineExample):
        visual = {
            "kind": example.visual, "id": "visual", "first_mark": example.first_mark,
            "last_mark": example.last_mark, "divisions_per_unit": example.divisions_per_unit,
        }
        template = "number_line_fraction_v1"
    else:
        raise studytomy_http_error(409, STALE_VERSION, "The reviewed practice example is invalid.")
    components = [
        {"kind": "explanation", "id": "explanation", "text": example.explanation},
        visual,
        {"kind": "question", "id": "question", "question": {
            "kind": "rational", "id": uuid4(), "prompt": example.prompt,
        }},
    ]
    next_basis = FractionEvaluationBasis(
        template=template, numerator=example.numerator, denominator=example.denominator,
        reviewed_example_id=example.example_id,
    )
    return components, next_basis, next_index + 1 < len(examples)


async def apply_learning_action(
    request: Request, db: AsyncSession, viewer: PublicUser, activity_uuid: str, operation: ActionRequest,
) -> LearningSurface:
    source = await _authorize_source(request, db, viewer, activity_uuid)
    record = await _owned_surface(db, source, viewer.id, operation.surface_id, lock=True)
    # Membership, content or policy may have changed while another tab held the lock.
    source = await _authorize_source(request, db, viewer, activity_uuid)
    if (source.org_id, source.course_uuid) != (record.org_id, record.course_uuid):
        raise studytomy_http_error(409, STALE_VERSION, "The lesson has changed. Reload the practice activity.")

    key = str(operation.request_id)
    digest = canonical_request_hash(operation)
    replay = await db.get(StudyLearningAction, (record.surface_uuid, key))
    if replay is not None:
        if replay.request_hash != digest:
            raise studytomy_http_error(409, IDEMPOTENCY_CONFLICT, "This request ID already has a different action.")
        return _project_surface(source, record, replay)

    latest = await _latest_action(db, record.surface_uuid)
    surface = _project_surface(source, record, latest)
    if operation.expected_revision != surface.revision:
        raise studytomy_http_error(409, STALE_VERSION, "Practice changed. Reload it before trying again.")
    if operation.action not in surface.actions:
        raise studytomy_http_error(409, "PRACTICE_ACTION_UNAVAILABLE", "This action is unavailable for this question.")
    saved_basis = (await db.execute(
        select(StudyLearningAction.evaluation_basis)
        .where(
            StudyLearningAction.surface_uuid == record.surface_uuid,
            StudyLearningAction.evaluation_basis.is_not(None),
        )
        .order_by(StudyLearningAction.revision.desc()).limit(1)
    )).scalar_one_or_none()
    basis = FractionEvaluationBasis.model_validate(saved_basis or record.evaluation_basis)
    reviewed = next((item for item in source.approved_examples if item.example_id == basis.reviewed_example_id), None)
    if reviewed is None or Fraction(reviewed.numerator, reviewed.denominator) != Fraction(basis.numerator, basis.denominator):
        raise studytomy_http_error(409, STALE_VERSION, "The saved answer key no longer matches approved practice.")

    data = surface.model_dump()
    components = data["components"]
    answer = None
    assisted = None
    next_basis = None
    if isinstance(operation, SubmitAnswer):
        answer = operation.answer.strip()
        correct = evaluate_fraction(answer, basis)
        raw = latest.presentation if latest is not None else record.presentation
        assisted = any(item["kind"] == "hint" for item in raw["components"])
        components.append({
            "kind": "feedback", "id": "feedback",
            "outcome": "correct" if correct else "incorrect",
            "attempt_id": uuid4(),
            "text": reviewed.correct_feedback if correct else reviewed.incorrect_feedback,
        })
        current_index = next(i for i, item in enumerate(source.approved_examples) if item.example_id == reviewed.example_id)
        has_next = current_index + 1 < len(source.approved_examples)
        data["actions"] = (["next_question"] if correct and has_next else []) if correct else ["retry_practice"]
    elif operation.action == "request_hint":
        if not source.allow_hints or not reviewed.hint:
            raise studytomy_http_error(409, "PRACTICE_ACTION_UNAVAILABLE", "A hint is not approved for this example.")
        components.append({"kind": "hint", "id": "hint", "text": reviewed.hint})
        data["actions"] = ["submit_answer"]
    else:
        data["components"], next_basis, _ = _next_question(
            surface, basis, source, retry=operation.action == "retry_practice",
        )
        next_example_id = next_basis.reviewed_example_id if next_basis else None
        next_example = next((item for item in source.approved_examples if item.example_id == next_example_id), None)
        data["actions"] = [] if next_example is None else ["submit_answer"]
        if next_example is not None and source.allow_hints and next_example.hint:
            data["actions"].append("request_hint")

    data["revision"] = surface.revision + 1
    updated = LearningSurface.model_validate(data)
    db.add(StudyLearningAction(
        surface_uuid=record.surface_uuid, request_uuid=key, request_hash=digest,
        revision=updated.revision, action=operation.action, answer=answer, assisted=assisted,
        presentation=updated.model_dump(mode="json"),
        evaluation_basis=None if next_basis is None else next_basis.model_dump(mode="json"),
        retention_policy_id=record.retention_policy_id,
        retention_category=record.retention_category,
        retention_expires_at=record.retention_expires_at,
    ))
    await db.commit()
    return updated
