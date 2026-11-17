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
    _authorize_source,
    recover_learning_surface,
    save_learning_surface,
)
from src.services.ai.llm import generate, model_for_tier
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
    """Instantiate fixed templates with an exact answer basis owned by the server."""
    if selection.visual == "fraction_explorer":
        visual = {"kind": "fraction_explorer", "id": "visual", "parts": 4, "selected": 2}
        explanation = "Each part has the same size. Compare the selected parts with the whole."
        prompt = "Write a fraction equivalent to 2/4."
        template = "equivalent_fractions_v1"
        denominator = 2
    else:
        visual = {
            "kind": "number_line", "id": "visual",
            "first_mark": 0, "last_mark": 1, "divisions_per_unit": 4,
        }
        explanation = "The equally spaced marks divide the interval into equal parts."
        prompt = "What fraction is the first mark to the right of zero?"
        template = "number_line_fraction_v1"
        denominator = 4

    components = [{"kind": "explanation", "id": "explanation", "text": explanation}, visual]
    actions = []
    basis = None
    if start.mode == "practice":
        components.append({
            "kind": "question", "id": "question",
            "question": {"kind": "rational", "id": uuid4(), "prompt": prompt},
        })
        actions = ["submit_answer"]
        if source.allow_hints:
            actions.append("request_hint")
        basis = FractionEvaluationBasis(template=template, numerator=1, denominator=denominator)
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
                model_name=model_for_tier("standard"),
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
