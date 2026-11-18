"""Resolve durable guidance authority and build a bounded lesson context."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import ActivityTypeEnum
from src.db.organizations import Organization
from src.db.studytomy.adaptive import StudyConcept
from src.db.studytomy.adaptive_evidence import StudyAttemptItem, StudyLearningAttempt
from src.db.studytomy.adaptive_guidance import StudyTutorSession
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import StudyPolicyKey
from src.db.studytomy.privacy import StudyConsentPurpose
from src.db.users import PublicUser
from src.services.courses.activities.dynamic_content import strip_dynamic_content
from src.services.studytomy.adaptive_attempt_access import (
    ResolvedManagedReleaseSource,
    lock_attempt_learner,
    resolve_managed_release_lesson_source,
)
from src.services.studytomy.adaptive_curriculum import curriculum_activity_digest
from src.services.studytomy.adaptive_curriculum_review import _REVIEW_POLICIES
from src.services.studytomy.adaptive_diagnostics import _policy_body
from src.services.studytomy.adaptive_evidence import _bounded_mastery, _watermark
from src.services.studytomy.adaptive_mastery import MasteryState
from src.services.studytomy.adaptive_progress import _read_lesson_source, _read_policy
from src.services.studytomy.consents import has_current_consent_scope
from src.services.studytomy.errors import (
    POLICY_NOT_APPROVED,
    STALE_VERSION,
    studytomy_http_error,
)
from src.services.studytomy.learners import _current_policies, _latest_decisions
from src.services.studytomy.policies import lock_organization

_SCOPE = "adaptive_guidance"
_POLICIES = (*_REVIEW_POLICIES, StudyPolicyKey.D_08, StudyPolicyKey.D_13)
_PROSE_NODES = frozenset(
    {
        "doc",
        "paragraph",
        "heading",
        "blockquote",
        "bulletList",
        "orderedList",
        "listItem",
        "text",
    }
)


class TutorStart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_uuid: Annotated[str, Field(min_length=1, max_length=89)]
    course_uuid: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    course_release_uuid: Annotated[
        str, Field(pattern=r"^course_release_[0-9a-f-]{36}$")
    ] | None = None
    concept_uuid: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    practice_attempt_uuid: Annotated[str, Field(min_length=1, max_length=64)] | None = (
        None
    )

    @model_validator(mode="after")
    def require_release_scope_pair(self):
        if (self.course_uuid is None) != (self.course_release_uuid is None):
            raise ValueError("Course and release identifiers must be provided together.")
        return self


class TutorOptions(BaseModel):
    mode: Literal["legacy", "guided", "unavailable"]
    actions: list[Literal["explain", "hint", "check_understanding"]] = Field(
        default_factory=list
    )
    reason: Literal["assessment_in_progress", "guidance_unavailable"] | None = None
    practice_attempt_uuid: str | None = None


async def read_tutor_options(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    activity_uuid: str,
    *,
    course_uuid: str | None = None,
    course_release_uuid: str | None = None,
) -> TutorOptions:
    if (course_uuid is None) != (course_release_uuid is None):
        raise HTTPException(404, "Guidance source not found.")
    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    attempt = StudyLearningAttempt
    practice_uuid = (
        await db.execute(
            select(attempt.uuid)
            .join(
                StudyLearnerProfile,
                StudyLearnerProfile.id == attempt.learner_profile_id,
            )
            .where(
                attempt.org_id == org.id,
                StudyLearnerProfile.org_id == org.id,
                StudyLearnerProfile.user_id == actor.id,
                attempt.source_activity_uuid == activity_uuid,
                attempt.source_release_uuid == course_release_uuid,
                *(
                    (attempt.source_course_uuid == course_uuid,)
                    if course_uuid is not None
                    else ()
                ),
                attempt.kind == "practice",
                attempt.status == "active",
                attempt.expires_at > now,
            )
            .order_by(attempt.started_at.desc(), attempt.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    try:
        await _binding(
            request,
            db,
            org,
            actor,
            TutorStart(
                activity_uuid=activity_uuid,
                course_uuid=course_uuid,
                course_release_uuid=course_release_uuid,
                practice_attempt_uuid=practice_uuid,
            ),
            now,
        )
    except HTTPException as error:
        if error.status_code not in (403, 404, 409):
            raise
        code = error.detail.get("code") if isinstance(error.detail, dict) else None
        return TutorOptions(
            mode="unavailable",
            reason="assessment_in_progress"
            if code == "ASSESSMENT_IN_PROGRESS"
            else "guidance_unavailable",
        )
    return TutorOptions(
        mode="guided",
        actions=["hint"] if practice_uuid else ["explain", "check_understanding"],
        practice_attempt_uuid=practice_uuid,
    )


class TutorClose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Annotated[int, Field(strict=True, ge=1)]


class TutorSessionRead(BaseModel):
    uuid: str
    mode: Literal["guided"] = "guided"
    activity_uuid: str
    step: int
    version: int
    closed_at: datetime | None


@dataclass(frozen=True, slots=True)
class GuidanceContext:
    lesson_title: str
    lesson_prose: str
    concept_title: str
    objective: str
    mastery_state: MasteryState
    independent_family_count: int
    context_limited: bool = False
    practice_linked: bool = False


@dataclass(frozen=True, slots=True)
class GuidanceBinding:
    profile_id: int
    curriculum_id: int
    concept_id: int
    course_uuid: str
    course_release_uuid: str | None
    source_binding_digest: str | None
    activity_digest: str
    practice_id: int | None
    eligibility_id: int
    policy_ids: list[int]
    context: GuidanceContext


def _lesson_prose(content: dict) -> tuple[str, bool]:
    safe = strip_dynamic_content(content)
    fragments = []
    remaining = 4000
    stack = [safe]
    visited = 0
    limited = False
    while stack and remaining > 0 and visited < 1000:
        node = stack.pop()
        visited += 1
        if not isinstance(node, dict) or node.get("type") not in _PROSE_NODES:
            continue
        text = node.get("text")
        if node["type"] == "text" and isinstance(text, str) and text.strip():
            piece = text.strip()[:remaining]
            limited |= len(text.strip()) > len(piece)
            fragments.append(piece)
            remaining -= len(piece) + 1
        children = node.get("content")
        if isinstance(children, list):
            limited |= len(children) > 1000
            stack.extend(reversed(children[:1000]))
    return "\n".join(fragments), limited or bool(stack)


async def _binding(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    body: TutorStart,
    now: datetime,
    *,
    concept_id: int | None = None,
    practice_id: int | None = None,
) -> GuidanceBinding:
    profile = await lock_attempt_learner(db, org, actor, now, _SCOPE)
    if not await has_current_consent_scope(
        db, org, profile, StudyConsentPurpose.AI_ASSISTANCE, _SCOPE, now
    ):
        raise studytomy_http_error(
            403, POLICY_NOT_APPROVED, "Current guidance consent is required."
        )
    release_source: ResolvedManagedReleaseSource | None = None
    if body.course_uuid is not None and body.course_release_uuid is not None:
        release_source = await resolve_managed_release_lesson_source(
            db,
            org,
            actor,
            body.course_uuid,
            body.course_release_uuid,
            body.activity_uuid,
            now,
        )
        snapshot = release_source.activity_snapshot
        if snapshot.get("activity_sub_type") != "SUBTYPE_DYNAMIC_PAGE":
            raise HTTPException(404, "Guidance source not found.")
        concept_rows = (
            await db.execute(
                select(StudyConcept).where(
                    StudyConcept.org_id == org.id,
                    StudyConcept.curriculum_revision_id
                    == release_source.curriculum.id,
                    StudyConcept.uuid.in_(release_source.concept_uuids),
                    StudyConcept.archived.is_(False),
                )
            )
        ).scalars().all()
        selected_concepts = [
            row
            for row in concept_rows
            if (body.concept_uuid is None or row.uuid == body.concept_uuid)
            and (concept_id is None or row.id == concept_id)
        ]
        if not selected_concepts:
            raise HTTPException(404, "Guidance source not found.")
        curriculum = release_source.curriculum
        course_uuid = body.course_uuid
        lesson_title = snapshot.get("name")
        lesson_content = snapshot.get("content")
        if not isinstance(lesson_title, str) or not isinstance(lesson_content, dict):
            raise HTTPException(404, "Guidance source not found.")
        activity_type = ActivityTypeEnum.TYPE_DYNAMIC
        activity_digest = release_source.activity_digest
        concept = selected_concepts[0]
    else:
        mappings = await _read_lesson_source(
            request, db, org, actor, body.activity_uuid
        )
        selected = [
            row
            for row in mappings
            if (body.concept_uuid is None or row[1].uuid == body.concept_uuid)
            and (concept_id is None or row[1].id == concept_id)
        ]
        if not selected or len({row[4].id for row in selected}) != 1:
            raise HTTPException(404, "Guidance source not found.")
        _, concept, activity, course, curriculum = selected[0]
        course_uuid = course.course_uuid
        lesson_title = activity.name
        lesson_content = activity.content
        activity_type = activity.activity_type
        activity_digest = curriculum_activity_digest(activity)
        selected_concepts = [row[1] for row in selected]
    practice_requested = (
        body.practice_attempt_uuid is not None or practice_id is not None
    )
    if course_uuid != curriculum.owner_course_uuid or (
        activity_type != ActivityTypeEnum.TYPE_DYNAMIC
        and not (
            practice_requested
            and activity_type == ActivityTypeEnum.TYPE_ASSIGNMENT
        )
    ):
        raise HTTPException(404, "Guidance source not found.")
    attempt = StudyLearningAttempt
    restricted_attempt = (
        await db.execute(
            select(attempt.id)
            .where(
                attempt.org_id == org.id,
                attempt.learner_profile_id == profile.id,
                or_(
                    attempt.kind == "diagnostic",
                    (attempt.kind == "assignment")
                    & (attempt.source_activity_uuid == body.activity_uuid),
                ),
                attempt.status == "active",
                attempt.expires_at > now,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if restricted_attempt is not None:
        raise studytomy_http_error(
            409,
            "ASSESSMENT_IN_PROGRESS",
            "Finish the assessment before opening guidance.",
        )
    if practice_requested:
        if not await has_current_consent_scope(
            db,
            org,
            profile,
            StudyConsentPurpose.LEARNING_TERMS,
            "adaptive_practice",
            now,
        ):
            raise HTTPException(404, "Practice attempt not found.")
        identifier = (
            attempt.id == practice_id
            if practice_id is not None
            else attempt.uuid == body.practice_attempt_uuid
        )
        practice_rows = (
            await db.execute(
                select(attempt.id, StudyAttemptItem.concept_id)
                .join(StudyAttemptItem, StudyAttemptItem.attempt_id == attempt.id)
                .where(
                    identifier,
                    StudyAttemptItem.org_id == org.id,
                    StudyAttemptItem.concept_id.in_(
                        {mapped_concept.id for mapped_concept in selected_concepts}
                    ),
                    attempt.org_id == org.id,
                    attempt.learner_profile_id == profile.id,
                    attempt.curriculum_revision_id == curriculum.id,
                    attempt.source_course_uuid == course_uuid,
                    attempt.source_activity_uuid == body.activity_uuid,
                    attempt.source_release_uuid == body.course_release_uuid,
                    attempt.source_binding_digest
                    == (
                        release_source.source_binding_digest
                        if release_source is not None
                        else None
                    ),
                    attempt.kind == "practice",
                    attempt.status == "active",
                    attempt.expires_at > now,
                )
            )
        ).all()
        if not practice_rows:
            raise HTTPException(404, "Practice attempt not found.")
        practice_id = practice_rows[0][0]
        issued_concepts = {row[1] for row in practice_rows}
        concept = next(row for row in selected_concepts if row.id in issued_concepts)
    policies = await _current_policies(
        db,
        org,
        (
            *_POLICIES,
            StudyPolicyKey.D_01,
            StudyPolicyKey.D_03,
            StudyPolicyKey.D_05,
            StudyPolicyKey.D_12,
            StudyPolicyKey.D_18,
        ),
        now,
    )
    assistance = _policy_body(policies, StudyPolicyKey.D_08).specified_value(
        "assistance"
    )
    guidance = assistance.get(_SCOPE) if isinstance(assistance, dict) else None
    if not isinstance(guidance, dict) or guidance.get("enabled") is not True:
        raise studytomy_http_error(
            409, POLICY_NOT_APPROVED, "Reviewed guidance assistance rules are required."
        )
    policy, _, _ = await _read_policy(db, org, curriculum, now)
    eligibility = next(
        decision
        for decision in await _latest_decisions(db, profile)
        if decision.eligibility_kind == "learning"
    )
    watermark = await _watermark(db, org.id, profile.id)
    mastery = await _bounded_mastery(
        db, org.id, profile.id, concept.id, policy, watermark, now
    )
    prose, limited = (
        _lesson_prose(lesson_content)
        if activity_type == ActivityTypeEnum.TYPE_DYNAMIC
        else ("", False)
    )
    return GuidanceBinding(
        profile_id=profile.id,
        curriculum_id=curriculum.id,
        concept_id=concept.id,
        course_uuid=course_uuid,
        course_release_uuid=body.course_release_uuid,
        source_binding_digest=(
            release_source.source_binding_digest
            if release_source is not None
            else None
        ),
        activity_digest=activity_digest,
        practice_id=practice_id,
        eligibility_id=eligibility.id,
        policy_ids=sorted(int(row.id) for row in policies),
        context=GuidanceContext(
            lesson_title[:200],
            prose,
            concept.title,
            concept.objective,
            mastery.state,
            mastery.eligible_count,
            limited or len(lesson_title) > 200,
            practice_id is not None,
        ),
    )


async def start_tutor_session(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    body: TutorStart,
) -> TutorSessionRead:
    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    bound = await _binding(request, db, org, actor, body, now)
    row = StudyTutorSession(
        org_id=org.id,
        learner_profile_id=bound.profile_id,
        curriculum_revision_id=bound.curriculum_id,
        concept_id=bound.concept_id,
        activity_uuid=body.activity_uuid,
        course_uuid=bound.course_uuid,
        source_release_uuid=bound.course_release_uuid,
        source_binding_digest=bound.source_binding_digest,
        activity_digest=bound.activity_digest,
        practice_attempt_id=bound.practice_id,
        eligibility_decision_id=bound.eligibility_id,
        policy_version_ids=bound.policy_ids,
        created_at=now,
    )
    db.add(row)
    await db.flush()
    response = _read(row)
    await db.commit()
    return response


async def _owned_session(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    session_uuid: str,
) -> StudyTutorSession:
    row = (
        await db.execute(
            select(StudyTutorSession)
            .join(
                StudyLearnerProfile,
                StudyLearnerProfile.id == StudyTutorSession.learner_profile_id,
            )
            .where(
                StudyTutorSession.org_id == org.id,
                StudyTutorSession.uuid == session_uuid,
                StudyLearnerProfile.org_id == org.id,
                StudyLearnerProfile.user_id == actor.id,
            )
            .with_for_update(of=StudyTutorSession)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Guidance session not found.")
    return row


async def load_guidance_context(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    session_uuid: str,
) -> tuple[StudyTutorSession, GuidanceContext]:
    await lock_organization(db, org.id)
    row = await _owned_session(db, org, actor, session_uuid)
    if row.closed_at is not None:
        raise HTTPException(404, "Guidance session not found.")
    bound = await _binding(
        request,
        db,
        org,
        actor,
        TutorStart(
            activity_uuid=row.activity_uuid,
            course_uuid=row.course_uuid if row.source_release_uuid is not None else None,
            course_release_uuid=row.source_release_uuid,
        ),
        datetime.now(UTC),
        concept_id=row.concept_id,
        practice_id=row.practice_attempt_id,
    )
    if (
        row.curriculum_revision_id != bound.curriculum_id
        or row.course_uuid != bound.course_uuid
        or row.source_release_uuid != bound.course_release_uuid
        or row.source_binding_digest != bound.source_binding_digest
        or row.activity_digest != bound.activity_digest
        or row.policy_version_ids != bound.policy_ids
        or row.eligibility_decision_id != bound.eligibility_id
    ):
        raise studytomy_http_error(
            409, STALE_VERSION, "Open a new guidance session for the current lesson."
        )
    return row, bound.context


def _read(row: StudyTutorSession) -> TutorSessionRead:
    return TutorSessionRead(
        uuid=row.uuid,
        activity_uuid=row.activity_uuid,
        step=row.step,
        version=row.version,
        closed_at=row.closed_at,
    )


async def close_tutor_session(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    session_uuid: str,
    body: TutorClose,
) -> TutorSessionRead:
    await lock_organization(db, org.id)
    row = await _owned_session(db, org, actor, session_uuid)
    if row.version != body.expected_version:
        raise studytomy_http_error(409, STALE_VERSION, "Reload the guidance session.")
    if row.closed_at is None:
        row.closed_at = datetime.now(UTC)
        row.version += 1
        await db.flush()
    response = _read(row)
    await db.commit()
    return response
