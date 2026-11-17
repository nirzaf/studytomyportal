"""Source binding and recovery for native lesson AI practice."""

from dataclasses import dataclass, replace
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import Activity, ActivitySubTypeEnum, ActivityTypeEnum
from src.db.courses.courses import Course
from src.db.organization_config import OrganizationConfig
from src.db.studytomy.practice import StudyLearningAction, StudyLearningSurface
from src.db.users import PublicUser
from src.security.features_utils.resolve import resolve_feature
from src.services.ai.ai import _authorize_activity_ai_access
from src.services.ai.schemas.learning_surface import (
    LearningAvailability,
    LearningSurface,
    QuestionCard,
)
from src.services.courses.activities.dynamic_context import render_for_reader
from src.services.studytomy.errors import (
    POLICY_NOT_APPROVED,
    STALE_VERSION,
    studytomy_http_error,
)
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.privacy import require_studytomy_learner_ai_policy


class FractionEvaluationBasis(BaseModel):
    """Private evaluator input issued by the application, never serialized to the learner."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    template: Literal["equivalent_fractions_v1", "number_line_fraction_v1"]
    numerator: Annotated[int, Field(ge=-144, le=144)]
    denominator: Annotated[int, Field(ge=1, le=12)]


@dataclass(frozen=True)
class PracticeSource:
    activity_uuid: str
    course_uuid: str
    org_id: int
    identity: str
    approved_visuals: frozenset[str] = frozenset()
    allow_hints: bool = False


@dataclass(frozen=True)
class PracticePermission:
    approved_visuals: frozenset[str]
    allow_hints: bool = False


async def require_learning_practice_policy(
    db: AsyncSession, viewer: PublicUser, source: PracticeSource,
) -> PracticePermission:
    await require_studytomy_learner_ai_policy(db, viewer)
    # The existing guard excludes protected learners but cannot approve a
    # specific practice source, assessment context, or retention purpose yet.
    raise studytomy_http_error(
        409, POLICY_NOT_APPROVED,
        "Lesson practice is unavailable until the required learning and AI policies are approved.",
    )


async def _authorize_source(
    request: Request, db: AsyncSession, viewer: PublicUser, activity_uuid: str,
) -> PracticeSource:
    if not isinstance(viewer, PublicUser) or viewer.id <= 0:
        raise HTTPException(403, "A learner session is required.")

    result = await db.execute(
        select(Activity, Course)
        .join(Course, Activity.course_id == Course.id)
        .where(Activity.activity_uuid == activity_uuid, Activity.org_id == Course.org_id)
        .limit(2)
        .execution_options(populate_existing=True)
    )
    rows = result.all()
    if len(rows) != 1:
        raise HTTPException(404, "Lesson not found.")
    activity, course = rows[0]

    await _authorize_activity_ai_access(request, course, activity, course.org_id, viewer, db)
    if (
        not course.published or not activity.published
        or activity.activity_type != ActivityTypeEnum.TYPE_DYNAMIC
        or activity.activity_sub_type != ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE
    ):
        raise HTTPException(404, "Lesson not found.")

    content = await render_for_reader(
        request, activity.content, course=course, current_user=viewer, db_session=db,
        activity_name=activity.name, activity_uuid=activity.activity_uuid, is_editor=False,
    )
    source = PracticeSource(
        activity_uuid=activity.activity_uuid,
        course_uuid=course.course_uuid,
        org_id=course.org_id,
        identity=canonical_request_hash({
            "version": activity.current_version,
            "title": activity.name,
            "reader_content": content,
        }),
    )
    permission = await require_learning_practice_policy(db, viewer, source)
    config = (await db.execute(
        select(OrganizationConfig.config).where(OrganizationConfig.org_id == course.org_id).limit(1)
    )).scalar_one_or_none()
    if config is None or not resolve_feature("ai", config, course.org_id)["enabled"]:
        raise studytomy_http_error(403, "PRACTICE_AI_DISABLED", "Lesson AI is disabled for this organization.")
    return replace(source, approved_visuals=permission.approved_visuals, allow_hints=permission.allow_hints)


def _stale_practice() -> HTTPException:
    return studytomy_http_error(409, STALE_VERSION, "The lesson has changed. Start a new practice activity.")


async def save_learning_surface(
    request: Request, db: AsyncSession, viewer: PublicUser, surface: LearningSurface,
    basis: FractionEvaluationBasis | None,
) -> LearningSurface:
    """Stage a server-issued initial surface in the caller's transaction."""
    source = await _authorize_source(request, db, viewer, surface.activity_id)
    if surface.source_identity != source.identity:
        raise _stale_practice()
    if not any(item.kind in source.approved_visuals for item in surface.components):
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "This lesson visual is not approved.")
    if surface.revision != 1 or any(item.kind in {"hint", "feedback"} for item in surface.components):
        raise ValueError("An initial surface cannot contain hints or feedback.")
    question = next((item.question for item in surface.components if isinstance(item, QuestionCard)), None)
    if surface.mode == "practice" and (basis is None or question is None or question.kind != "rational"):
        raise ValueError("Initial practice requires a supported private evaluation basis.")
    if surface.mode == "explore" and basis is not None:
        raise ValueError("Exploration has no evaluation basis.")

    record = StudyLearningSurface(
        surface_uuid=str(surface.surface_id), org_id=source.org_id, user_id=viewer.id,
        request_uuid=str(surface.request_id), course_uuid=source.course_uuid,
        activity_uuid=source.activity_uuid, source_identity=source.identity,
        presentation=surface.model_dump(mode="json"),
        evaluation_basis=None if basis is None else basis.model_dump(mode="json"),
    )
    db.add(record)
    await db.flush()
    return surface


async def recover_learning_surface(
    request: Request, db: AsyncSession, viewer: PublicUser, activity_uuid: str, surface_id: UUID,
    *, initial: bool = False,
) -> LearningSurface:
    """Recheck current authority before returning a learner's exact saved surface."""
    source = await _authorize_source(request, db, viewer, activity_uuid)
    record = await _owned_surface(db, source, viewer.id, surface_id)
    latest = None if initial else await _latest_action(db, record.surface_uuid)
    return _project_surface(source, record, latest)


async def get_learning_availability(
    request: Request, db: AsyncSession, viewer: PublicUser, activity_uuid: str,
) -> LearningAvailability:
    source = await _authorize_source(request, db, viewer, activity_uuid)
    if not source.approved_visuals.intersection({"fraction_explorer", "number_line"}):
        return LearningAvailability(available=False)
    records = (await db.execute(
        select(StudyLearningSurface).where(
            StudyLearningSurface.org_id == source.org_id,
            StudyLearningSurface.user_id == viewer.id,
            StudyLearningSurface.activity_uuid == source.activity_uuid,
            StudyLearningSurface.course_uuid == source.course_uuid,
            StudyLearningSurface.source_identity == source.identity,
        ).order_by(StudyLearningSurface.created_at.desc(), StudyLearningSurface.surface_uuid.desc()).limit(1)
    )).scalar_one_or_none()
    if records is None:
        return LearningAvailability(available=True)
    latest = await _latest_action(db, records.surface_uuid)
    try:
        surface = _project_surface(source, records, latest)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        # A previously issued visual can lose approval while another remains available.
        return LearningAvailability(available=True)
    return LearningAvailability(available=True, surface=surface)


async def _owned_surface(
    db: AsyncSession, source: PracticeSource, user_id: int, surface_id: UUID, *, lock: bool = False,
) -> StudyLearningSurface:
    query = select(StudyLearningSurface).where(
        StudyLearningSurface.surface_uuid == str(surface_id),
        StudyLearningSurface.org_id == source.org_id,
        StudyLearningSurface.user_id == user_id,
        StudyLearningSurface.course_uuid == source.course_uuid,
        StudyLearningSurface.activity_uuid == source.activity_uuid,
    )
    if lock:
        query = query.with_for_update()
    record = (await db.execute(query)).scalar_one_or_none()
    if record is None:
        raise HTTPException(404, "Practice activity not found.")
    return record


async def _latest_action(db: AsyncSession, surface_uuid: str) -> StudyLearningAction | None:
    return (await db.execute(
        select(StudyLearningAction).where(StudyLearningAction.surface_uuid == surface_uuid)
        .order_by(StudyLearningAction.revision.desc()).limit(1)
    )).scalar_one_or_none()


def _project_surface(
    source: PracticeSource, record: StudyLearningSurface, action: StudyLearningAction | None,
) -> LearningSurface:
    if record.source_identity != source.identity:
        raise _stale_practice()
    surface = LearningSurface.model_validate(record.presentation if action is None else action.presentation)
    if not any(item.kind in source.approved_visuals for item in surface.components):
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "This lesson visual is not approved.")
    if "request_hint" in surface.actions and not source.allow_hints:
        data = surface.model_dump()
        data["actions"] = [item for item in surface.actions if item != "request_hint"]
        surface = LearningSurface.model_validate(data)
    if not source.allow_hints and any(item.kind == "hint" for item in surface.components):
        data = surface.model_dump()
        data["components"] = [item.model_dump() for item in surface.components if item.kind != "hint"]
        surface = LearningSurface.model_validate(data)
    return surface
