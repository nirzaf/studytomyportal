"""Opt-in fractions topic check for an authorized, reviewed managed PDF release."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import Activity
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import StudyPolicyKey
from src.db.studytomy.practice import StudyLearningAction, StudyLearningSurface
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.security.org_auth import verify_org_admin
from src.security.rbac import AccessAction, AccessContext, check_resource_access
from src.services.ai.learning_actions import evaluate_fraction
from src.services.ai.learning_practice import (
    FractionEvaluationBasis,
    PracticePermission,
    PracticeSource,
    ReviewedFractionExample,
    ReviewedTopicCheck,
    _latest_action,
    require_learning_practice_policy,
)
from src.services.studytomy.access_policy import _assessed_pass_delivery, _project_authorized_media
from src.services.studytomy.adaptive_attempt_access import resolve_managed_release_lesson_source
from src.services.studytomy.adaptive_progress import read_staff_mastery
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import _as_utc, lock_organization


class TopicScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_uuid: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    course_release_uuid: Annotated[str, StringConstraints(pattern=r"^course_release_[0-9a-f-]{36}$")]
    activity_uuid: Annotated[str, StringConstraints(min_length=1, max_length=89)]


class TopicSubmission(TopicScope):
    operation_uuid: UUID
    binding_id: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    expected_revision: Annotated[int, Field(strict=True, ge=1, le=2)]
    answer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=48)]


class TopicVisual(BaseModel):
    parts: int
    selected: int


class TopicQuestion(BaseModel):
    prompt: str
    visual: TopicVisual


class TopicCheckView(BaseModel):
    binding_id: str
    episode_uuid: UUID | None = None
    revision: int = 1
    phase: Literal["initial", "recheck", "complete", "needs_review"]
    question: TopicQuestion | None = None
    reteaching: str | None = None
    teaching_visual: TopicVisual | None = None
    pdf_page: int
    outcome: Literal["correct", "incorrect"] | None = None
    assisted: bool = False


class TopicTimelineEvent(BaseModel):
    kind: Literal["initial", "recheck"]
    prompt: str
    outcome: Literal["correct", "incorrect"]
    reteaching: str | None = None
    teaching_visual: TopicVisual | None = None
    assisted: bool
    at: datetime


class TopicTimeline(BaseModel):
    scope: TopicScope
    concept_uuid: str
    activity_version: int
    pdf_page: int
    episode_uuid: UUID | None
    complete: bool
    events: list[TopicTimelineEvent]


@dataclass(frozen=True, slots=True)
class _Binding:
    identity: str
    policy: PracticePermission
    review: ReviewedTopicCheck
    initial: ReviewedFractionExample
    recheck: ReviewedFractionExample
    activity_version: int
    lesson_id: int


def _unavailable() -> HTTPException:
    return HTTPException(404, "Topic check unavailable.")


async def _bind(db: AsyncSession, org: Organization, learner: PublicUser, scope: TopicScope) -> _Binding:
    now = datetime.now(UTC)
    source = await resolve_managed_release_lesson_source(
        db, org, learner, scope.course_uuid, scope.course_release_uuid, scope.activity_uuid, now,
    )
    if len(source.concept_uuids) != 1:
        raise _unavailable()
    auth = source.authorization
    policy = await require_learning_practice_policy(
        db, learner, PracticeSource(scope.activity_uuid, scope.course_uuid, org.id, source.source_binding_digest),
    )
    review = policy.topic_check
    if (
        not policy.allow_practice or review is None or auth.release.uuid != scope.course_release_uuid
        or review.release_snapshot_sha256 != auth.release.snapshot_sha256
        or policy.review_policy_id is None or policy.review_policy_id != auth.release.rights_policy_id
        or policy.concept_uuid != source.concept_uuids[0]
        or policy.curriculum_revision_uuid != source.curriculum.uuid
        or policy.activity_digest != source.activity_digest
        or policy.retention_policy_id is None or policy.retention_hours is None
        or policy.retention_category != "lesson_visual_practice"
    ):
        raise _unavailable()
    media_policy = (await _current_policies(db, org, (StudyPolicyKey.D_15,), now))[0]
    assessed, _ = await _assessed_pass_delivery(
        db, auth.release, auth.progression_policy, learner.id, scope.activity_uuid, lock_result=True,
    )
    _, media, kind = _project_authorized_media(
        auth.release, auth.progression_policy, media_policy, scope.activity_uuid, None, assessed_pass_verified=assessed,
    )
    if kind != "application/pdf" or media["sha256"] != review.pdf_sha256:
        raise _unavailable()
    selected = {
        example.example_id: example for example in policy.approved_examples
        if isinstance(example, ReviewedFractionExample)
    }
    initial = selected.get(review.initial_example_id)
    recheck = selected.get(review.recheck_example_id)
    lesson_id = source.activity_snapshot.get("id")
    if initial is None or recheck is None or type(lesson_id) is not int:
        raise _unavailable()
    identity = canonical_request_hash({
        "schema": "managed_pdf_topic_check_v1", "source": source.source_binding_digest,
        "release": auth.release.snapshot_sha256, "media_policy": media_policy.id,
        "practice_policies": policy.policy_version_ids, "review": review.model_dump(),
        "initial": initial.model_dump(), "recheck": recheck.model_dump(),
    })
    return _Binding(identity, policy, review, initial, recheck, source.activity_version, lesson_id)


def _visual(example: ReviewedFractionExample) -> TopicVisual:
    return TopicVisual(parts=example.parts, selected=example.selected)


def _question(example: ReviewedFractionExample) -> TopicQuestion:
    return TopicQuestion(prompt=example.prompt, visual=_visual(example))


def _initial(binding: _Binding, episode_uuid: UUID | None = None) -> TopicCheckView:
    return TopicCheckView(
        binding_id=binding.identity, episode_uuid=episode_uuid, phase="initial", pdf_page=binding.review.pdf_page,
        question=_question(binding.initial),
    )


def _answer(binding: _Binding, view: TopicCheckView, answer: str) -> TopicCheckView:
    example = binding.initial if view.phase == "initial" else binding.recheck
    correct = evaluate_fraction(answer, FractionEvaluationBasis(
        template="equivalent_fractions_v1", numerator=example.numerator, denominator=example.denominator,
        reviewed_example_id=example.example_id,
    ))
    if view.phase == "initial" and not correct:
        return TopicCheckView(
            binding_id=binding.identity, episode_uuid=view.episode_uuid, revision=2,
            phase="recheck", pdf_page=binding.review.pdf_page,
            outcome="incorrect", assisted=True, question=_question(binding.recheck),
            reteaching=binding.review.reteaching,
            teaching_visual=_visual(binding.initial) if binding.review.show_visual and binding.policy.allow_explore else None,
        )
    return TopicCheckView(
        binding_id=binding.identity, episode_uuid=view.episode_uuid, revision=view.revision + 1,
        phase="complete" if correct else "needs_review",
        pdf_page=binding.review.pdf_page, outcome="correct" if correct else "incorrect",
        assisted=view.phase == "recheck",
    )


async def _record(db: AsyncSession, org: Organization, user_id: int, scope: TopicScope, binding: _Binding):
    return (await db.execute(
        select(StudyLearningSurface).where(
            StudyLearningSurface.org_id == org.id, StudyLearningSurface.user_id == user_id,
            StudyLearningSurface.course_uuid == scope.course_uuid,
            StudyLearningSurface.activity_uuid == scope.activity_uuid,
            StudyLearningSurface.source_identity == binding.identity,
        ).with_for_update()
    )).scalar_one_or_none()


def _check_retention(record: StudyLearningSurface) -> None:
    if record.retention_expires_at is None or _as_utc(record.retention_expires_at) <= datetime.now(UTC):
        raise _unavailable()


async def read_topic_check(db: AsyncSession, org: Organization, learner: PublicUser, scope: TopicScope) -> TopicCheckView:
    binding = await _bind(db, org, learner, scope)
    record = await _record(db, org, learner.id, scope, binding)
    if record is None:
        return _initial(binding)
    _check_retention(record)
    action = await _latest_action(db, record.surface_uuid)
    return TopicCheckView.model_validate((action or record).presentation)


async def submit_topic_check(
    db: AsyncSession, org: Organization, learner: PublicUser, body: TopicSubmission,
) -> TopicCheckView:
    scope = TopicScope.model_validate(body.model_dump(include=set(TopicScope.model_fields)))
    binding = await _bind(db, org, learner, scope)
    if body.binding_id != binding.identity:
        raise HTTPException(409, "Topic check source changed. Reload the lesson.")
    record = await _record(db, org, learner.id, scope, binding)
    prior = None
    key = str(body.operation_uuid)
    digest = canonical_request_hash(body.model_dump(mode="json"))
    if record is not None:
        _check_retention(record)
        replay = await db.get(StudyLearningAction, (record.surface_uuid, key))
        if replay is not None:
            if replay.request_hash != digest:
                raise HTTPException(409, "Topic check request ID already used.")
            return TopicCheckView.model_validate(replay.presentation)
        prior = await _latest_action(db, record.surface_uuid)
        view = TopicCheckView.model_validate((prior or record).presentation)
    else:
        if body.expected_revision != 1:
            raise HTTPException(409, "Topic check changed. Reload the lesson.")
        episode = uuid4()
        view = _initial(binding, episode)
        created = datetime.now(UTC)
        record = StudyLearningSurface(
            surface_uuid=str(episode), request_uuid=str(episode), org_id=org.id, user_id=learner.id,
            activity_uuid=scope.activity_uuid, course_uuid=scope.course_uuid, source_identity=binding.identity,
            presentation=view.model_dump(mode="json"), created_at=created,
            retention_policy_id=binding.policy.retention_policy_id, retention_category=binding.policy.retention_category,
            retention_expires_at=created + timedelta(hours=binding.policy.retention_hours),
        )
        db.add(record)
        await db.flush()
    if view.revision != body.expected_revision or view.phase not in {"initial", "recheck"}:
        raise HTTPException(409, "Topic check changed. Reload the lesson.")
    updated = _answer(binding, view, body.answer)
    db.add(StudyLearningAction(
        surface_uuid=record.surface_uuid, request_uuid=key, request_hash=digest, revision=updated.revision,
        action="submit_answer", answer=body.answer, assisted=view.phase == "recheck",
        presentation=updated.model_dump(mode="json"), retention_policy_id=record.retention_policy_id,
        retention_category=record.retention_category, retention_expires_at=record.retention_expires_at,
    ))
    await db.commit()
    return updated


async def read_topic_timeline(
    request: Request, db: AsyncSession, org: Organization, staff: PublicUser, learner_uuid: str,
    scope: TopicScope,
) -> TopicTimeline:
    """Show a bounded lesson episode, not the learner's raw answers."""
    org = await lock_organization(db, org.id)
    membership = (await db.execute(
        select(UserOrganization).where(UserOrganization.org_id == org.id, UserOrganization.user_id == staff.id)
    )).scalar_one_or_none()
    if membership is None:
        raise _unavailable()
    activity = (await db.execute(
        select(Activity).join(Course, Course.id == Activity.course_id).where(
            Activity.org_id == org.id, Course.org_id == org.id,
            Activity.activity_uuid == scope.activity_uuid, Course.course_uuid == scope.course_uuid,
            Course.studytomy_managed.is_(True),
        )
    )).scalar_one_or_none()
    if activity is None:
        raise _unavailable()
    if (activity.extra_metadata or {}).get("studytomy_pdf_creator_uuid") != staff.user_uuid:
        try:
            await verify_org_admin(staff.id, org.id, db)
        except HTTPException as error:
            if error.status_code in {403, 404}:
                raise _unavailable() from None
            raise
    permission = await check_resource_access(
        request, db, staff, scope.course_uuid, AccessAction.UPDATE,
        context=AccessContext.DASHBOARD, raise_on_deny=False,
    )
    if not permission.allowed:
        raise _unavailable()
    learner = (await db.execute(
        select(User).join(StudyLearnerProfile, StudyLearnerProfile.user_id == User.id).where(
            StudyLearnerProfile.org_id == org.id, StudyLearnerProfile.learner_uuid == learner_uuid,
            StudyLearnerProfile.status == "active",
        )
    )).scalar_one_or_none()
    if learner is None or learner.id == staff.id:
        raise _unavailable()
    try:
        binding = await _bind(db, org, PublicUser.model_validate(learner), scope)
        if binding.lesson_id != activity.id or not binding.policy.curriculum_revision_uuid:
            raise _unavailable()
        await read_staff_mastery(
            request, db, org, staff, learner_uuid, binding.policy.curriculum_revision_uuid, 0, 1,
        )
    except HTTPException as error:
        if error.status_code in {403, 404, 409, 410}:
            raise _unavailable() from None
        raise
    record = await _record(db, org, learner.id, scope, binding)
    events = []
    complete = False
    if record is not None:
        _check_retention(record)
        actions = (await db.execute(
            select(StudyLearningAction).where(StudyLearningAction.surface_uuid == record.surface_uuid)
            .order_by(StudyLearningAction.revision).limit(2)
        )).scalars().all()
        for action in actions:
            response = TopicCheckView.model_validate(action.presentation)
            complete = response.phase == "complete" and response.outcome == "correct"
            if response.outcome is not None:
                events.append(TopicTimelineEvent(
                    kind="recheck" if action.assisted else "initial",
                    prompt=(binding.recheck if action.assisted else binding.initial).prompt,
                    outcome=response.outcome, assisted=bool(action.assisted),
                    reteaching=response.reteaching, teaching_visual=response.teaching_visual, at=action.created_at,
                ))
    return TopicTimeline(
        scope=scope, concept_uuid=binding.policy.concept_uuid or "", activity_version=binding.activity_version,
        pdf_page=binding.review.pdf_page, episode_uuid=UUID(record.surface_uuid) if record else None,
        complete=complete, events=events,
    )
