"""Select a reviewed lesson visual and commit its typed surface before delivery."""

import asyncio
import json
import logging
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.studytomy.practice import StudyLearningSurface
from src.db.users import PublicUser
from src.security.features_utils.usage import refund_ai_credit, reserve_ai_credit
from src.services.ai.learning_practice import (
    FractionEvaluationBasis,
    PracticeSource,
    ReviewedFractionExample,
    _authorize_source,
    recover_learning_surface,
    save_learning_surface,
)
from src.services.ai.llm import generate
from src.services.ai.schemas.learning_surface import (
    LearningPresentation,
    LearningSurface,
    StartLearningSurface,
)
from src.services.security.rate_limiting import enforce_ai_rate_limit
from src.services.studytomy.errors import IDEMPOTENCY_CONFLICT, studytomy_http_error
from src.services.studytomy.idempotency import claim_idempotency, complete_idempotency

_OPERATION = "start_lesson_learning_surface"
_VISUALS = {"fraction_explorer", "number_line"}
logger = logging.getLogger(__name__)


def _instantiate(
    start: StartLearningSurface, source: PracticeSource, selection: LearningPresentation,
) -> tuple[LearningSurface, FractionEvaluationBasis | None]:
    """Build the learner surface only from the exact reviewed example selected by the provider."""
    example = next(
        (item for item in source.approved_examples if item.visual == selection.visual),
        None,
    )
    if example is None:
        raise studytomy_http_error(422, "LEARNING_SURFACE_UNSUPPORTED", "No reviewed example supports this visual.")
    if start.mode == "explore" and not source.allow_explore:
        raise studytomy_http_error(409, "PRACTICE_ACTION_UNAVAILABLE", "Visual exploration is not approved for this lesson.")
    if start.mode == "practice" and not source.allow_practice:
        raise studytomy_http_error(409, "PRACTICE_ACTION_UNAVAILABLE", "Supported practice is not approved for this lesson.")

    if isinstance(example, ReviewedFractionExample):
        visual = {
            "kind": example.visual, "id": "visual",
            "parts": example.parts, "selected": example.selected,
        }
        template = "equivalent_fractions_v1"
    else:
        visual = {
            "kind": example.visual, "id": "visual",
            "first_mark": example.first_mark,
            "last_mark": example.last_mark,
            "divisions_per_unit": example.divisions_per_unit,
        }
        template = "number_line_fraction_v1"

    components = [
        {"kind": "explanation", "id": "explanation", "text": example.explanation},
        visual,
    ]
    actions = []
    basis = None
    if start.mode == "practice":
        components.append({
            "kind": "question", "id": "question",
            "question": {"kind": "rational", "id": uuid4(), "prompt": example.prompt},
        })
        actions = ["submit_answer"]
        if source.allow_hints and example.hint:
            actions.append("request_hint")
        basis = FractionEvaluationBasis(
            template=template, numerator=example.numerator, denominator=example.denominator,
            reviewed_example_id=example.example_id,
        )
    return LearningSurface.model_validate({
        "schema_version": 1, "surface_id": uuid4(), "turn_id": uuid4(),
        "request_id": start.request_id, "revision": 1, "activity_id": source.activity_uuid,
        "source_identity": source.identity, "mode": start.mode, "components": components,
        "actions": actions,
    }), basis


async def start_learning_surface(
    request: Request, db: AsyncSession, viewer: PublicUser, start: StartLearningSurface,
) -> LearningSurface:
    source = await _authorize_source(request, db, viewer, start.activity_uuid)
    if (start.mode == "explore" and not source.allow_explore) or (
        start.mode == "practice" and not source.allow_practice
    ):
        raise studytomy_http_error(409, "PRACTICE_ACTION_UNAVAILABLE", "This lesson mode is not currently approved.")
    key = str(start.request_id)
    existing = (await db.execute(select(StudyLearningSurface).where(
        StudyLearningSurface.org_id == source.org_id,
        StudyLearningSurface.user_id == viewer.id,
        StudyLearningSurface.request_uuid == key,
    ))).scalar_one_or_none()
    if existing is not None:
        if existing.activity_uuid != source.activity_uuid or existing.presentation.get("mode") != start.mode:
            raise studytomy_http_error(409, IDEMPOTENCY_CONFLICT, "This request ID belongs to a different activity.")
        return await recover_learning_surface(
            request, db, viewer, start.activity_uuid, UUID(existing.surface_uuid), initial=True,
        )

    allowed = sorted(source.approved_visuals & _VISUALS)
    if not allowed:
        raise studytomy_http_error(422, "LEARNING_SURFACE_UNSUPPORTED", "This lesson does not have an approved visual.")
    replay = await claim_idempotency(
        db, org_id=source.org_id, actor_user_id=viewer.id, operation=_OPERATION,
        idempotency_key=key, request_payload=start,
    )
    if replay is not None:
        if replay.resource_kind != "learning_surface" or replay.resource_uuid is None:
            raise studytomy_http_error(409, IDEMPOTENCY_CONFLICT, "This request has no recoverable lesson visual.")
        return await recover_learning_surface(
            request, db, viewer, start.activity_uuid, UUID(replay.resource_uuid), initial=True,
        )

    enforce_ai_rate_limit(viewer.id, source.org_id)
    try:
        await reserve_ai_credit(source.org_id, db)
    except HTTPException as exc:
        raise studytomy_http_error(
            exc.status_code, "LEARNING_SURFACE_ALLOWANCE", "Lesson visual allowance is unavailable.",
        ) from None
    committed = False
    try:
        # The approved source determines the allowlist. Send only that list
        # and the mode, without lesson prose, answers, identity or chat history.
        try:
            output = await asyncio.wait_for(generate(
                model_name=source.approved_model,
                system_prompt="Select one visual from the approved list for the requested learning mode.",
                user_prompt=json.dumps({"mode": start.mode, "approved_visuals": allowed}),
                output_type=LearningPresentation,
            ), timeout=20)
            selection = LearningPresentation.model_validate(output)
        except Exception:  # noqa: BLE001 - provider adapters raise SDK-specific errors
            raise studytomy_http_error(
                503, "LEARNING_SURFACE_UNAVAILABLE", "The lesson visual is unavailable. Try again or use text help.",
            ) from None
        if selection.visual not in allowed:
            raise studytomy_http_error(422, "LEARNING_SURFACE_UNSUPPORTED", "The selected visual is not approved.")

        surface, basis = _instantiate(start, source, selection)
        await save_learning_surface(request, db, viewer, surface, basis)
        await complete_idempotency(
            db, org_id=source.org_id, actor_user_id=viewer.id, operation=_OPERATION,
            idempotency_key=key, resource_kind="learning_surface", resource_uuid=str(surface.surface_id),
        )
        await db.commit()
        committed = True
        return surface
    finally:
        if not committed:
            try:
                refund_ai_credit(source.org_id)
            except Exception:  # noqa: BLE001 - a refund error must preserve the original failure
                logger.warning("Lesson visual credit refund failed")
