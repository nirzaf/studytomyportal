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
_HINTS = {
    "equivalent_fractions_v1": "Try dividing the top and bottom numbers by the same number.",
    "number_line_fraction_v1": "Count the equal spaces from zero to one. Then count the spaces from zero to the mark.",
}


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
    surface: LearningSurface, basis: FractionEvaluationBasis, retry: bool,
) -> tuple[list[dict], FractionEvaluationBasis]:
    components = [item.model_dump() for item in surface.components if item.kind not in {"hint", "feedback"}]
    visual = next(item for item in components if item["kind"] in {"fraction_explorer", "number_line"})
    question = next(item for item in components if item["kind"] == "question")
    previous = Fraction(basis.numerator, basis.denominator)
    preferred = [Fraction(1, 2), Fraction(1, 3)] if retry else [Fraction(3, 4), Fraction(2, 3)]
    value = next(candidate for candidate in preferred if candidate != previous)
    if visual["kind"] == "fraction_explorer":
        selected, parts = value.numerator * 2, value.denominator * 2
        visual.update(parts=parts, selected=selected)
        prompt = f"Write a fraction equivalent to {selected}/{parts}."
    else:
        visual.update(first_mark=0, last_mark=1, divisions_per_unit=value.denominator)
        positions = {1: "first", 2: "second", 3: "third"}
        prompt = f"What fraction is the {positions[value.numerator]} mark to the right of zero?"
    question["question"] = {"kind": "rational", "id": uuid4(), "prompt": prompt}
    return components, FractionEvaluationBasis(
        template=basis.template, numerator=value.numerator, denominator=value.denominator,
    )


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
            "text": "These fractions represent the same value." if correct else "Compare the value of each fraction.",
        })
        data["actions"] = ["next_question"] if correct else ["retry_practice"]
    elif operation.action == "request_hint":
        components.append({"kind": "hint", "id": "hint", "text": _HINTS[basis.template]})
        data["actions"] = ["submit_answer"]
    else:
        data["components"], next_basis = _next_question(surface, basis, operation.action == "retry_practice")
        data["actions"] = ["submit_answer", "request_hint"] if source.allow_hints else ["submit_answer"]

    data["revision"] = surface.revision + 1
    updated = LearningSurface.model_validate(data)
    db.add(StudyLearningAction(
        surface_uuid=record.surface_uuid, request_uuid=key, request_hash=digest,
        revision=updated.revision, action=operation.action, answer=answer, assisted=assisted,
        presentation=updated.model_dump(mode="json"),
        evaluation_basis=None if next_basis is None else next_basis.model_dump(mode="json"),
    ))
    await db.commit()
    return updated
