"""Read current learner mastery without exposing evidence or private answers."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import Activity
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.adaptive import (
    StudyConcept,
    StudyConceptActivity,
    StudyConceptPrerequisite,
    StudyCurriculumRevision,
)
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import StudyPolicyKey
from src.db.studytomy.privacy import StudyConsentPurpose
from src.db.users import PublicUser
from src.security.rbac import AccessAction, AccessContext, check_resource_access
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_studytomy_staff_action,
)
from src.services.ai.rag.access import can_read_activity
from src.services.studytomy.adaptive_access import (
    _is_learning_eligible,
    adaptive_enabled,
)
from src.services.studytomy.adaptive_attempt_access import lock_attempt_learner
from src.services.studytomy.adaptive_curriculum import curriculum_activity_digest
from src.services.studytomy.adaptive_curriculum_review import (
    _REVIEW_POLICIES,
    CurriculumPublish,
    _require_exact_policy_approval,
)
from src.services.studytomy.adaptive_diagnostics import _policy_body
from src.services.studytomy.adaptive_evidence import _bounded_mastery, _watermark
from src.services.studytomy.adaptive_mastery import (
    MasteryPolicy,
    MasteryState,
    resolve_mastery_policy,
)
from src.services.studytomy.consents import has_current_consent_scope
from src.services.studytomy.errors import POLICY_NOT_APPROVED, studytomy_http_error
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization


class MasteryRead(BaseModel):
    concept_uuid: str
    code: str
    title: str
    state: MasteryState
    score: Decimal | None
    independent_family_count: int
    success_count: int
    assisted_count: int
    unknown_count: int
    review_due: bool
    last_assessed_at: datetime | None
    limitation: str


class MasteryPage(BaseModel):
    curriculum_revision_uuid: str
    mastery_policy_uuid: str
    policy_version: int
    evidence_watermark: int
    as_of: datetime
    concepts: list[MasteryRead]
    next_offset: int | None


async def _read_policy(
    db: AsyncSession,
    org: Organization,
    curriculum: StudyCurriculumRevision,
    now: datetime,
) -> tuple[MasteryPolicy, int, Literal["return_to_lesson"] | None]:
    policies = await _current_policies(
        db, org, (*_REVIEW_POLICIES, StudyPolicyKey.D_13), now
    )
    review = _policy_body(policies, StudyPolicyKey.D_16).specified_value(
        "review_approvals"
    )
    entries = (
        review.get("adaptive_curriculum_reviews", [])
        if isinstance(review, dict)
        else []
    )
    refs = (
        [
            entry.get("evidence_reference")
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("revision_uuid") == curriculum.uuid
            and entry.get("digest") == curriculum.content_digest
        ]
        if isinstance(entries, list)
        else []
    )
    try:
        command = CurriculumPublish(
            expected_version=curriculum.version,
            expected_digest=curriculum.content_digest,
            review_evidence=refs[0] if len(refs) == 1 else None,
        )
    except ValidationError as error:
        raise studytomy_http_error(
            409, POLICY_NOT_APPROVED, "Current curriculum review is required."
        ) from error
    _require_exact_policy_approval(policies, curriculum, command)
    d09 = _policy_body(policies, StudyPolicyKey.D_09)
    rules = d09.specified_value("mastery_gate")
    settings = (
        rules.get("adaptive_curriculum_policies") if isinstance(rules, dict) else None
    )
    policy_uuid = settings.get(curriculum.uuid) if isinstance(settings, dict) else None
    if not isinstance(policy_uuid, str):
        raise studytomy_http_error(
            409,
            POLICY_NOT_APPROVED,
            "A reviewed mastery policy for this curriculum is required.",
        )
    policy = resolve_mastery_policy(d09, policy_uuid)
    version = next(
        policy_row.version
        for policy_row in policies
        if policy_row.policy_key == StudyPolicyKey.D_09.value
    )
    support = (
        "return_to_lesson"
        if isinstance(rules, dict)
        and rules.get("adaptive_recommendation_support") == "return_to_lesson"
        else None
    )
    return policy, version, support


async def read_learner_mastery(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    revision_uuid: str | None,
    offset: int,
    limit: int,
    *,
    activity_uuid: str | None = None,
) -> MasteryPage:
    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    profile = await lock_attempt_learner(db, org, actor, now, "adaptive_mastery")
    lesson_concepts = None
    if activity_uuid is not None:
        source = await _read_lesson_source(request, db, org, actor, activity_uuid)
        if len({row[4].id for row in source}) != 1:
            raise HTTPException(404, "Learning source not found.")
        revision_uuid = source[0][4].uuid
        lesson_concepts = {row[1].id for row in source}
    curriculum, course = await _published_curriculum(db, org, revision_uuid)
    permission = await check_resource_access(
        request,
        db,
        actor,
        course.course_uuid,
        AccessAction.READ,
        context=AccessContext.DASHBOARD,
        raise_on_deny=False,
    )
    if not permission.allowed:
        raise HTTPException(404, "Curriculum not found.")
    return await _mastery_page(
        db,
        org,
        profile,
        curriculum,
        now,
        offset,
        limit,
        lesson_concepts=lesson_concepts,
    )


async def _published_curriculum(
    db: AsyncSession,
    org: Organization,
    revision_uuid: str | None,
) -> tuple[StudyCurriculumRevision, Course]:
    pair = (
        await db.execute(
            select(StudyCurriculumRevision, Course)
            .join(
                Course, Course.course_uuid == StudyCurriculumRevision.owner_course_uuid
            )
            .where(
                StudyCurriculumRevision.org_id == org.id,
                StudyCurriculumRevision.uuid == revision_uuid,
                StudyCurriculumRevision.status == "published",
                Course.org_id == org.id,
                Course.published.is_(True),
            )
            .limit(2)
        )
    ).all()
    if len(pair) != 1:
        raise HTTPException(404, "Curriculum not found.")
    return pair[0][0], pair[0][1]


async def read_staff_mastery(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    learner_uuid: str,
    revision_uuid: str,
    offset: int,
    limit: int,
) -> MasteryPage:
    unavailable = HTTPException(404, "Learning record not found.")
    await lock_organization(db, org.id)
    if not await adaptive_enabled(db, org):
        raise unavailable
    now = datetime.now(UTC)
    try:
        curriculum, course = await _published_curriculum(db, org, revision_uuid)
    except HTTPException as error:
        if error.status_code != 404:
            raise
        raise unavailable from error
    profile = (
        await db.execute(
            select(StudyLearnerProfile)
            .where(
                StudyLearnerProfile.org_id == org.id,
                StudyLearnerProfile.learner_uuid == learner_uuid,
                StudyLearnerProfile.status == "active",
                StudyLearnerProfile.control_mode == "independent",
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if profile is None:
        raise unavailable
    for kind, uuid in (
        ("course", course.course_uuid),
        ("learner", profile.learner_uuid),
    ):
        try:
            await authorize_studytomy_staff_action(
                db,
                actor,
                org,
                StudytomyCapabilityName.ASSESSMENT_MODERATION,
                target_scope_kind=kind,
                target_resource_uuid=uuid,
                subject_user_id=profile.user_id,
                hide_existence_on_denial=True,
                now=now,
            )
        except HTTPException as error:
            if error.status_code not in (403, 404):
                raise
            raise unavailable from error
    permission = await check_resource_access(
        request,
        db,
        actor,
        course.course_uuid,
        AccessAction.UPDATE,
        context=AccessContext.DASHBOARD,
        raise_on_deny=False,
    )
    if not permission.allowed:
        raise unavailable
    if not await _is_learning_eligible(
        db, org, profile, now
    ) or not await has_current_consent_scope(
        db,
        org,
        profile,
        StudyConsentPurpose.LEARNING_TERMS,
        "adaptive_staff_mastery",
        now,
    ):
        raise unavailable
    return await _mastery_page(db, org, profile, curriculum, now, offset, limit)


async def _mastery_page(
    db: AsyncSession,
    org: Organization,
    profile: StudyLearnerProfile,
    curriculum: StudyCurriculumRevision,
    now: datetime,
    offset: int,
    limit: int,
    *,
    lesson_concepts: set[int] | None = None,
) -> MasteryPage:
    policy, version, _ = await _read_policy(db, org, curriculum, now)
    concepts = (
        (
            await db.execute(
                select(StudyConcept)
                .where(
                    StudyConcept.org_id == org.id,
                    StudyConcept.curriculum_revision_id == curriculum.id,
                    StudyConcept.archived.is_(False),
                    *(
                        (StudyConcept.id.in_(lesson_concepts),)
                        if lesson_concepts is not None
                        else ()
                    ),
                )
                .order_by(StudyConcept.sequence, StudyConcept.id)
                .offset(offset)
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    watermark = await _watermark(db, org.id, profile.id)
    result = []
    for concept in concepts[:limit]:
        mastery = await _bounded_mastery(
            db, org.id, profile.id, concept.id, policy, watermark, now
        )
        result.append(
            MasteryRead(
                concept_uuid=concept.uuid,
                code=concept.code,
                title=concept.title,
                state=mastery.state,
                score=mastery.score,
                independent_family_count=mastery.eligible_count,
                success_count=mastery.success_count,
                assisted_count=mastery.assisted_count,
                unknown_count=mastery.unknown_count,
                review_due=mastery.review_due,
                last_assessed_at=mastery.latest_independent_at,
                limitation="independent_evidence_only"
                if mastery.eligible_count
                else "no_independent_evidence",
            )
        )
    return MasteryPage(
        curriculum_revision_uuid=curriculum.uuid,
        mastery_policy_uuid=policy.policy_uuid,
        policy_version=version,
        evidence_watermark=watermark,
        as_of=now,
        concepts=result,
        next_offset=offset + limit if len(concepts) > limit else None,
    )


RecommendationReason = Literal[
    "prerequisite_gap", "practice_needed", "not_yet_assessed", "review_due"
]


class Recommendation(BaseModel):
    activity_uuid: str
    course_uuid: str
    activity_title: str
    concept_uuid: str
    reason: RecommendationReason


class NoRecommendation(BaseModel):
    reason: Literal["no_accessible_activity"] = "no_accessible_activity"
    support_action: Literal["return_to_lesson"] | None = None


async def _lesson_mappings(
    db: AsyncSession,
    org_id: int,
    *,
    revision_id: int | None = None,
    activity_uuid: str | None = None,
):
    mapping = StudyConceptActivity
    query = (
        select(mapping, StudyConcept, Activity, Course, StudyCurriculumRevision)
        .join(StudyConcept, StudyConcept.id == mapping.concept_id)
        .join(
            StudyCurriculumRevision,
            StudyCurriculumRevision.id == mapping.curriculum_revision_id,
        )
        .join(Activity, Activity.id == mapping.activity_id)
        .join(Course, Course.id == Activity.course_id)
        .where(
            mapping.org_id == org_id,
            StudyCurriculumRevision.org_id == org_id,
            StudyCurriculumRevision.status == "published",
            StudyConcept.archived.is_(False),
            mapping.archived.is_(False),
            Activity.org_id == org_id,
            Activity.published.is_(True),
            Activity.activity_uuid == mapping.activity_uuid,
            Activity.current_version == mapping.activity_version,
            Course.org_id == org_id,
            Course.id == mapping.course_id,
            Course.course_uuid == mapping.course_uuid,
            Course.published.is_(True),
        )
        .order_by(StudyConcept.sequence, StudyConcept.id, mapping.id)
    )
    if revision_id is not None:
        query = query.where(mapping.curriculum_revision_id == revision_id)
    if activity_uuid is not None:
        query = query.where(mapping.activity_uuid == activity_uuid)
    rows = (await db.execute(query.execution_options(populate_existing=True))).all()
    return [
        row
        for row in rows
        if row[0].activity_digest == curriculum_activity_digest(row[2])
    ]


async def _can_recommend(
    request: Request,
    db: AsyncSession,
    actor: PublicUser,
    course: Course,
    activity_id: int,
) -> bool:
    try:
        return await can_read_activity(request, actor, course, activity_id, db)
    except HTTPException as error:
        if error.status_code not in (403, 404):
            raise
        return False


async def _read_lesson_source(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    activity_uuid: str,
):
    source = await _lesson_mappings(db, org.id, activity_uuid=activity_uuid)
    if not source:
        raise HTTPException(404, "Learning source not found.")
    _, _, activity, course, _ = source[0]
    if not await _can_recommend(request, db, actor, course, activity.id):
        raise HTTPException(404, "Learning source not found.")
    return source


async def read_learner_recommendations(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    activity_uuid: str,
) -> list[Recommendation | NoRecommendation]:
    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    profile = await lock_attempt_learner(db, org, actor, now, "adaptive_mastery")
    source = await _read_lesson_source(request, db, org, actor, activity_uuid)
    _, _, activity, _, curriculum = source[0]
    if len({row[4].id for row in source}) != 1:
        return [NoRecommendation()]
    policy, _, support = await _read_policy(db, org, curriculum, now)
    source_concepts = {row[1].id for row in source}
    edge = StudyConceptPrerequisite
    prerequisites = set(
        (
            await db.execute(
                select(edge.prerequisite_id).where(
                    edge.org_id == org.id,
                    edge.curriculum_revision_id == curriculum.id,
                    edge.concept_id.in_(source_concepts),
                )
            )
        ).scalars()
    )
    candidates = [
        row
        for row in await _lesson_mappings(db, org.id, revision_id=curriculum.id)
        if row[2].id != activity.id
    ]
    watermark = await _watermark(db, org.id, profile.id)
    ranked: dict[int, tuple[int, RecommendationReason]] = {}
    for concept in {row[1].id: row[1] for row in candidates}.values():
        mastery = await _bounded_mastery(
            db, org.id, profile.id, concept.id, policy, watermark, now
        )
        rank = None
        if concept.id in prerequisites and mastery.state != "mastered":
            rank = (0, "prerequisite_gap")
        elif mastery.state == "developing":
            rank = (1, "practice_needed")
        elif mastery.state == "not_assessed":
            rank = (2, "not_yet_assessed")
        elif mastery.review_due:
            rank = (3, "review_due")
        if rank is not None:
            ranked[concept.id] = rank

    eligible = [row for row in candidates if row[1].id in ranked]
    eligible.sort(key=lambda row: ranked[row[1].id])
    results: list[Recommendation | NoRecommendation] = []
    checked: set[int] = set()
    recommended_concepts: set[int] = set()
    for _, concept, candidate, candidate_course, _ in eligible:
        if candidate.id in checked or concept.id in recommended_concepts:
            continue
        checked.add(candidate.id)
        if not await _can_recommend(request, db, actor, candidate_course, candidate.id):
            continue
        results.append(
            Recommendation(
                activity_uuid=candidate.activity_uuid,
                course_uuid=candidate_course.course_uuid,
                activity_title=candidate.name,
                concept_uuid=concept.uuid,
                reason=ranked[concept.id][1],
            )
        )
        recommended_concepts.add(concept.id)
        if len(results) == 3:
            break
    return results or [NoRecommendation(support_action=support)]
