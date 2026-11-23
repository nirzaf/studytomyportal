"""Source binding and recovery for native lesson AI practice."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from fractions import Fraction
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import Activity, ActivitySubTypeEnum, ActivityTypeEnum
from src.db.courses.courses import Course
from src.db.organization_config import OrganizationConfig
from src.db.organizations import Organization
from src.db.studytomy.adaptive import StudyConcept, StudyConceptActivity, StudyCurriculumRevision
from src.db.studytomy.governance import StudyPolicyBody, StudyPolicyKey, StudyPolicyVersion
from src.db.studytomy.practice import StudyLearningAction, StudyLearningSurface
from src.db.studytomy.privacy import StudyConsentPurpose
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser
from src.security.features_utils.resolve import resolve_feature
from src.services.ai.ai import _authorize_activity_reader_access
from src.services.ai.llm import model_for_tier
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
from src.services.studytomy.adaptive_attempt_access import PRACTICE_SCOPE, lock_attempt_learner
from src.services.studytomy.adaptive_curriculum import curriculum_activity_digest
from src.services.studytomy.adaptive_curriculum_review import (
    CurriculumPublish,
    _CurriculumApproval,
    _require_exact_policy_approval,
)
from src.services.studytomy.consents import has_current_consent_scope
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.learning_practice_retention import (
    PracticeRemovalRules as _PracticeRemovalPolicy,
    PracticeRetentionRules as _PracticeRetentionPolicy,
)
from src.services.studytomy.policies import _as_utc
from src.db.studytomy.governance import StudyOwnerAssignment, StudyOwnerFunction, StudyOwnerStatus, StudyStaffScope
from src.db.users import User


class FractionEvaluationBasis(BaseModel):
    """Private evaluator input issued by the application, never serialized to the learner."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    template: Literal["equivalent_fractions_v1", "number_line_fraction_v1"]
    numerator: Annotated[int, Field(ge=-144, le=144)]
    denominator: Annotated[int, Field(ge=1, le=12)]
    reviewed_example_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,47}$")] | None = None


class ReviewedFractionExample(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    example_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,47}$")]
    visual: Literal["fraction_explorer"]
    parts: Annotated[int, Field(ge=1, le=12)]
    selected: Annotated[int, Field(ge=0, le=12)]
    numerator: Annotated[int, Field(ge=0, le=12)]
    denominator: Annotated[int, Field(ge=1, le=12)]
    prompt: Literal["What fraction of the whole is selected?"]
    explanation: Annotated[str, StringConstraints(min_length=1, max_length=600, pattern=r"^[^<>]+$")]
    hint: Annotated[str, StringConstraints(min_length=1, max_length=600, pattern=r"^[^<>]+$")] | None = None
    correct_feedback: Annotated[str, StringConstraints(min_length=1, max_length=600, pattern=r"^[^<>]+$")]
    incorrect_feedback: Annotated[str, StringConstraints(min_length=1, max_length=600, pattern=r"^[^<>]+$")]

    @model_validator(mode="after")
    def answer_matches_visual(self):
        if self.selected > self.parts or Fraction(self.selected, self.parts) != Fraction(self.numerator, self.denominator):
            raise ValueError("The reviewed fraction answer must match the selected visual parts")
        return self


class ReviewedNumberLineExample(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    example_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,47}$")]
    visual: Literal["number_line"]
    first_mark: Literal[0]
    last_mark: Literal[1]
    divisions_per_unit: Annotated[int, Field(ge=1, le=12)]
    numerator: Literal[1]
    denominator: Annotated[int, Field(ge=1, le=12)]
    prompt: Literal["What fraction is the first mark after zero?"]
    explanation: Annotated[str, StringConstraints(min_length=1, max_length=600, pattern=r"^[^<>]+$")]
    hint: Annotated[str, StringConstraints(min_length=1, max_length=600, pattern=r"^[^<>]+$")] | None = None
    correct_feedback: Annotated[str, StringConstraints(min_length=1, max_length=600, pattern=r"^[^<>]+$")]
    incorrect_feedback: Annotated[str, StringConstraints(min_length=1, max_length=600, pattern=r"^[^<>]+$")]

    @model_validator(mode="after")
    def answer_matches_visual(self):
        if self.denominator != self.divisions_per_unit:
            raise ValueError("The reviewed number-line answer must match its equal divisions")
        return self


ReviewedPracticeExample = ReviewedFractionExample | ReviewedNumberLineExample


class _PracticeReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    curriculum_revision_uuid: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    curriculum_digest: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    concept_uuid: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    course_uuid: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    activity_uuid: Annotated[str, StringConstraints(min_length=1, max_length=89)]
    activity_version: Annotated[int, Field(strict=True, ge=1)]
    activity_digest: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    rights_reference: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    evidence_reference: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    review_reference: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    reviewer_user_uuid: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    examples: Annotated[list[ReviewedPracticeExample], Field(min_length=1, max_length=12)]

    @model_validator(mode="after")
    def examples_are_unique(self):
        if len({example.example_id for example in self.examples}) != len(self.examples):
            raise ValueError("Reviewed example identifiers must be unique")
        return self


class _PracticeAssistance(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    enabled: Literal[True]
    visual_exploration: bool
    supported_practice: bool
    hints: bool


class _PracticeProviderApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    enabled: Literal[True]
    provider: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    model: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    base_url: Annotated[str, StringConstraints(min_length=1, max_length=300)] | None = None


class _ApprovedPracticePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    assistance: _PracticeAssistance
    provider: _PracticeProviderApproval
    retention: _PracticeRetentionPolicy
    removal: _PracticeRemovalPolicy


@dataclass(frozen=True)
class PracticeSource:
    activity_uuid: str
    course_uuid: str
    org_id: int
    identity: str
    approved_visuals: frozenset[str] = frozenset()
    allow_hints: bool = False
    curriculum_revision_uuid: str | None = None
    curriculum_digest: str | None = None
    mapping_uuid: str | None = None
    concept_uuid: str | None = None
    approval_reference: str | None = None
    rights_reference: str | None = None
    evidence_reference: str | None = None
    activity_digest: str | None = None
    policy_version_ids: tuple[int, ...] = ()
    approved_examples: tuple[ReviewedPracticeExample, ...] = ()
    allow_explore: bool = False
    allow_practice: bool = False
    approved_model: str | None = None
    retention_policy_id: int | None = None
    retention_category: str | None = None
    retention_hours: int | None = None


@dataclass(frozen=True)
class PracticePermission:
    approved_visuals: frozenset[str]
    allow_hints: bool = False
    approved_examples: tuple[ReviewedPracticeExample, ...] = ()
    allow_explore: bool = False
    allow_practice: bool = False
    approved_model: str | None = None
    retention_policy_id: int | None = None
    retention_category: str | None = None
    retention_hours: int | None = None
    curriculum_revision_uuid: str | None = None
    curriculum_digest: str | None = None
    mapping_uuid: str | None = None
    concept_uuid: str | None = None
    approval_reference: str | None = None
    rights_reference: str | None = None
    evidence_reference: str | None = None
    activity_digest: str | None = None
    policy_version_ids: tuple[int, ...] = ()


async def _resolve_practice_binding(
    db: AsyncSession,
    org: Organization,
    course: Course,
    activity: Activity,
    learner_user_id: int,
    now: datetime,
    policies: list[StudyPolicyVersion],
) -> tuple[StudyCurriculumRevision, StudyConcept, StudyConceptActivity, _PracticeReview]:
    digest = curriculum_activity_digest(activity)
    rows = (await db.execute(
        select(StudyCurriculumRevision, StudyConcept, StudyConceptActivity)
        .join(StudyConcept, StudyConcept.curriculum_revision_id == StudyCurriculumRevision.id)
        .join(StudyConceptActivity, StudyConceptActivity.concept_id == StudyConcept.id)
        .where(
            StudyCurriculumRevision.org_id == course.org_id,
            StudyCurriculumRevision.status == "published",
            StudyCurriculumRevision.owner_course_uuid == course.course_uuid,
            StudyConcept.archived.is_(False),
            StudyConceptActivity.org_id == course.org_id,
            StudyConceptActivity.curriculum_revision_id == StudyCurriculumRevision.id,
            StudyConceptActivity.course_id == course.id,
            StudyConceptActivity.course_uuid == course.course_uuid,
            StudyConceptActivity.activity_id == activity.id,
            StudyConceptActivity.activity_uuid == activity.activity_uuid,
            StudyConceptActivity.activity_version == activity.current_version,
            StudyConceptActivity.activity_digest == digest,
            StudyConceptActivity.usage == "practice",
            StudyConceptActivity.archived.is_(False),
        )
        .order_by(StudyConceptActivity.uuid)
        .limit(2)
    )).all()
    if len(rows) != 1:
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "Lesson practice requires exactly one current reviewed concept mapping.")
    curriculum, concept, mapping = rows[0]
    if not curriculum.content_digest or not curriculum.rights_reference:
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "The curriculum mapping is missing reviewed source evidence.")

    by_key = {policy.policy_key: policy for policy in policies}
    try:
        d16 = StudyPolicyBody.model_validate(by_key[StudyPolicyKey.D_16.value].body)
        reviews_value = d16.specified_value("review_approvals")
        curriculum_reviews = reviews_value.get("adaptive_curriculum_reviews") if isinstance(reviews_value, dict) else None
        if not isinstance(curriculum_reviews, list):
            raise ValueError("curriculum review list missing")
        parsed_curriculum_reviews = [_CurriculumApproval.model_validate(item) for item in curriculum_reviews]
        exact_curriculum = [item for item in parsed_curriculum_reviews if (
            item.revision_uuid == curriculum.uuid and item.digest == curriculum.content_digest
            and item.rights_reference == curriculum.rights_reference
        )]
        if len(exact_curriculum) != 1:
            raise ValueError("exact curriculum approval missing or ambiguous")
        _require_exact_policy_approval(
            policies,
            curriculum,
            CurriculumPublish(
                expected_version=curriculum.version,
                expected_digest=curriculum.content_digest,
                review_evidence=exact_curriculum[0].evidence_reference,
            ),
        )
        raw_reviews = reviews_value.get("lesson_visual_practice_reviews")
        if not isinstance(raw_reviews, list):
            raise ValueError("lesson visual practice review list missing")
        parsed_reviews = [_PracticeReview.model_validate(item) for item in raw_reviews]
    except (KeyError, TypeError, ValueError, ValidationError, HTTPException) as error:
        if isinstance(error, HTTPException):
            raise
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "Current D-02/D-16 policy does not approve this exact lesson practice.") from error

    matches = [review for review in parsed_reviews if (
        review.curriculum_revision_uuid == curriculum.uuid
        and review.curriculum_digest == curriculum.content_digest
        and review.concept_uuid == concept.uuid
        and review.course_uuid == course.course_uuid
        and review.activity_uuid == activity.activity_uuid
        and review.activity_version == activity.current_version
        and review.activity_digest == digest
        and review.rights_reference == curriculum.rights_reference
    )]
    if len(matches) != 1:
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "No unique D-16 review approves this exact concept, lesson revision and example set.")
    review = matches[0]
    reviewer = (await db.execute(
        select(User.id)
        .join(UserOrganization, UserOrganization.user_id == User.id)
        .join(StudyOwnerAssignment, StudyOwnerAssignment.user_id == User.id)
        .join(StudyStaffScope, StudyStaffScope.assignment_id == StudyOwnerAssignment.id)
        .where(
            User.user_uuid == review.reviewer_user_uuid,
            UserOrganization.org_id == course.org_id,
            StudyOwnerAssignment.org_id == course.org_id,
            StudyOwnerAssignment.responsibility == StudyOwnerFunction.EDUCATION.value,
            StudyOwnerAssignment.scope_kind == "organization",
            StudyOwnerAssignment.scope_uuid == org.org_uuid,
            StudyOwnerAssignment.status == StudyOwnerStatus.ACTIVE.value,
            StudyOwnerAssignment.effective_from <= now,
            (StudyOwnerAssignment.effective_to.is_(None) | (StudyOwnerAssignment.effective_to > now)),
            StudyStaffScope.org_id == course.org_id,
            StudyStaffScope.capability == "content_release_review",
            StudyStaffScope.scope_kind == "course",
            StudyStaffScope.scope_uuid == course.course_uuid,
            StudyStaffScope.status == "active",
            StudyStaffScope.is_exclusion.is_(False),
            (StudyOwnerAssignment.effective_to.is_(None) | (StudyOwnerAssignment.effective_to > now)),
        )
        .limit(2)
    )).scalars().all()
    if len(reviewer) != 1:
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "The named lesson reviewer is no longer an authorized education reviewer.")
    if reviewer[0] == learner_user_id:
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "The learner cannot be the educator reviewer for this lesson practice.")
    return curriculum, concept, mapping, review


async def require_learning_practice_policy(
    db: AsyncSession, viewer: PublicUser, source: PracticeSource,
) -> PracticePermission:
    now = datetime.now(UTC)
    org = await db.get(Organization, source.org_id)
    if org is None:
        raise HTTPException(404, "Lesson not found.")
    profile = await lock_attempt_learner(db, org, viewer, now, PRACTICE_SCOPE)
    if not await has_current_consent_scope(
        db, org, profile, StudyConsentPurpose.AI_ASSISTANCE, "lesson_visual_practice", now,
    ):
        raise studytomy_http_error(403, POLICY_NOT_APPROVED, "Current learner consent for lesson visual practice is required.")

    policies = await _current_policies(
        db, org,
        (StudyPolicyKey.D_02, StudyPolicyKey.D_08, StudyPolicyKey.D_10, StudyPolicyKey.D_13, StudyPolicyKey.D_16),
        now,
    )
    current = {policy.policy_key: policy for policy in policies}
    course_activity = (await db.execute(
        select(Course, Activity)
        .join(Activity, Activity.course_id == Course.id)
        .where(
            Course.org_id == org.id,
            Course.course_uuid == source.course_uuid,
            Activity.org_id == org.id,
            Activity.activity_uuid == source.activity_uuid,
        )
        .limit(2)
    )).all()
    if len(course_activity) != 1:
        raise HTTPException(404, "Lesson not found.")
    course, activity = course_activity[0]
    curriculum, concept, mapping, review = await _resolve_practice_binding(
        db, org, course, activity, viewer.id, now, policies,
    )
    d16 = StudyPolicyBody.model_validate(current[StudyPolicyKey.D_16.value].body)
    rights = d16.specified_value("content_rights")
    allowed_rights = rights.get("adaptive_curriculum_rights") if isinstance(rights, dict) else None
    if not isinstance(allowed_rights, list) or curriculum.rights_reference not in allowed_rights:
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "D-16 does not grant rights for this curriculum source.")
    try:
        d08 = StudyPolicyBody.model_validate(current[StudyPolicyKey.D_08.value].body)
        assistance = d08.specified_value("assistance")
        assistance = assistance.get("lesson_visual_practice") if isinstance(assistance, dict) else None
        d10 = StudyPolicyBody.model_validate(current[StudyPolicyKey.D_10.value].body)
        provider = d10.specified_value("review")
        provider = provider.get("lesson_visual_practice") if isinstance(provider, dict) else None
        retention_policy = StudyPolicyBody.model_validate(current[StudyPolicyKey.D_13.value].body)
        retention = retention_policy.specified_value("retention")
        retention = retention.get("lesson_visual_practice") if isinstance(retention, dict) else None
        removal_policy = retention_policy.specified_value("removal")
        removal = removal_policy.get("lesson_visual_practice") if isinstance(removal_policy, dict) else None
        approved = _ApprovedPracticePolicy(
            assistance=_PracticeAssistance.model_validate(assistance),
            provider=_PracticeProviderApproval.model_validate(provider),
            retention=_PracticeRetentionPolicy.model_validate(retention),
            removal=_PracticeRemovalPolicy.model_validate(removal),
        )
    except (ValidationError, TypeError, ValueError) as error:
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "Current D-08, D-10 and D-13 decisions do not approve lesson visual practice, provider use and retention.") from error

    from config.config import get_learnhouse_config
    ai_config = get_learnhouse_config().ai_config
    if (
        approved.provider.provider != ai_config.provider
        or approved.provider.model != model_for_tier("standard")
        or approved.provider.base_url != (ai_config.base_url or None)
    ):
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "The configured AI provider does not match the current D-10 practice approval.")

    examples = tuple(review.examples)
    visual_examples = frozenset(example.visual for example in examples)
    permitted_visuals = visual_examples if approved.assistance.visual_exploration else frozenset()
    if not approved.assistance.supported_practice:
        practice_enabled = False
    else:
        practice_enabled = bool(examples)
    if not approved.assistance.visual_exploration and not practice_enabled:
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "D-08 does not approve an available lesson practice mode.")
    return PracticePermission(
        approved_visuals=permitted_visuals if approved.assistance.visual_exploration else visual_examples,
        allow_hints=approved.assistance.hints and any(example.hint for example in examples),
        approved_examples=examples,
        allow_explore=approved.assistance.visual_exploration,
        allow_practice=practice_enabled,
        approved_model=approved.provider.model,
        retention_policy_id=int(current[StudyPolicyKey.D_13.value].id),
        retention_category=approved.retention.retention_category,
        retention_hours=approved.retention.retention_hours,
        curriculum_revision_uuid=curriculum.uuid,
        curriculum_digest=curriculum.content_digest,
        mapping_uuid=mapping.uuid,
        concept_uuid=concept.uuid,
        approval_reference=review.review_reference,
        rights_reference=review.rights_reference,
        evidence_reference=review.evidence_reference,
        activity_digest=review.activity_digest,
        policy_version_ids=tuple(sorted(int(policy.id) for policy in policies)),
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

    await _authorize_activity_reader_access(request, course, activity, course.org_id, viewer, db)
    if (
        not course.published or not activity.published
        or activity.activity_type != ActivityTypeEnum.TYPE_DYNAMIC
        or activity.activity_sub_type != ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE
    ):
        raise HTTPException(404, "Lesson not found.")

    source = PracticeSource(
        activity_uuid=activity.activity_uuid,
        course_uuid=course.course_uuid,
        org_id=course.org_id,
        identity=canonical_request_hash({"activity_uuid": activity.activity_uuid, "version": activity.current_version}),
    )
    permission = await require_learning_practice_policy(db, viewer, source)
    config = (await db.execute(
        select(OrganizationConfig.config).where(OrganizationConfig.org_id == course.org_id).limit(1)
    )).scalar_one_or_none()
    if config is None or not resolve_feature("ai", config, course.org_id)["enabled"]:
        raise studytomy_http_error(403, "PRACTICE_AI_DISABLED", "Lesson AI is disabled for this organization.")
    content = await render_for_reader(
        request, activity.content, course=course, current_user=viewer, db_session=db,
        activity_name=activity.name, activity_uuid=activity.activity_uuid, is_editor=False,
    )
    identity = canonical_request_hash({
        "activity_uuid": activity.activity_uuid,
        "activity_version": activity.current_version,
        "activity_digest": permission.activity_digest,
        "course_uuid": course.course_uuid,
        "reader_content": content,
        "curriculum_revision_uuid": permission.curriculum_revision_uuid,
        "curriculum_digest": permission.curriculum_digest,
        "mapping_uuid": permission.mapping_uuid,
        "concept_uuid": permission.concept_uuid,
        "approval_reference": permission.approval_reference,
        "rights_reference": permission.rights_reference,
        "evidence_reference": permission.evidence_reference,
        "policy_version_ids": permission.policy_version_ids,
        "examples": [example.model_dump(mode="json") for example in permission.approved_examples],
    })
    return replace(
        source,
        identity=identity,
        approved_visuals=permission.approved_visuals,
        allow_hints=permission.allow_hints,
        curriculum_revision_uuid=permission.curriculum_revision_uuid,
        curriculum_digest=permission.curriculum_digest,
        mapping_uuid=permission.mapping_uuid,
        concept_uuid=permission.concept_uuid,
        approval_reference=permission.approval_reference,
        rights_reference=permission.rights_reference,
        evidence_reference=permission.evidence_reference,
        activity_digest=permission.activity_digest,
        policy_version_ids=permission.policy_version_ids,
        approved_examples=permission.approved_examples,
        allow_explore=permission.allow_explore,
        allow_practice=permission.allow_practice,
        approved_model=permission.approved_model,
        retention_policy_id=permission.retention_policy_id,
        retention_category=permission.retention_category,
        retention_hours=permission.retention_hours,
    )


def _stale_practice() -> HTTPException:
    return studytomy_http_error(409, STALE_VERSION, "The lesson has changed. Start a new practice activity.")


def _visual_component_matches(component, example: ReviewedPracticeExample) -> bool:
    expected = {
        "kind": example.visual,
        "id": "visual",
        **(
            {"parts": example.parts, "selected": example.selected}
            if isinstance(example, ReviewedFractionExample)
            else {
                "first_mark": example.first_mark,
                "last_mark": example.last_mark,
                "divisions_per_unit": example.divisions_per_unit,
            }
        ),
    }
    return component.model_dump() == expected


async def save_learning_surface(
    request: Request, db: AsyncSession, viewer: PublicUser, surface: LearningSurface,
    basis: FractionEvaluationBasis | None,
) -> LearningSurface:
    """Stage a server-issued initial surface in the caller's transaction."""
    source = await _authorize_source(request, db, viewer, surface.activity_id)
    if surface.source_identity != source.identity:
        raise _stale_practice()
    if surface.revision != 1:
        raise ValueError("An initial surface must start at revision one.")
    expected_permission = source.allow_explore if surface.mode == "explore" else source.allow_practice
    if not expected_permission:
        raise studytomy_http_error(409, "PRACTICE_ACTION_UNAVAILABLE", "This lesson mode is not currently approved.")

    if surface.mode == "explore":
        if basis is not None:
            raise ValueError("Exploration cannot persist a private answer key.")
        candidate_examples = [item for item in source.approved_examples if item.visual in source.approved_visuals]
        # Explore surfaces carry no evaluator or question; select the unique reviewed visual.
        visuals = [item for item in surface.components if item.kind in {"fraction_explorer", "number_line"}]
        if len(visuals) != 1 or not any(
            _visual_component_matches(visuals[0], item) for item in candidate_examples
        ):
            raise studytomy_http_error(409, POLICY_NOT_APPROVED, "This lesson visual is not an exact approved example.")
        reviewed = next(item for item in candidate_examples if _visual_component_matches(visuals[0], item))
        explanations = [item for item in surface.components if item.kind == "explanation"]
        if len(explanations) != 1 or explanations[0].text != reviewed.explanation:
            raise studytomy_http_error(409, POLICY_NOT_APPROVED, "The explanation is not part of the approved lesson example.")
        expected_components = {"explanation", reviewed.visual}
        expected_actions: set[str] = set()
        if any(item.kind in {"question", "hint", "feedback"} for item in surface.components):
            raise ValueError("Explore surfaces cannot contain practice questions or feedback.")
    else:
        example_id = basis.reviewed_example_id if basis is not None else None
        example = next((item for item in source.approved_examples if item.example_id == example_id), None)
        if example is None:
            raise studytomy_http_error(409, POLICY_NOT_APPROVED, "The surface is not bound to an approved lesson example.")
        if example.visual not in source.approved_visuals or basis is None or basis.template != (
            "equivalent_fractions_v1" if isinstance(example, ReviewedFractionExample) else "number_line_fraction_v1"
        ) or Fraction(basis.numerator, basis.denominator) != Fraction(example.numerator, example.denominator):
            raise studytomy_http_error(409, POLICY_NOT_APPROVED, "The private answer does not match its approved example.")
        visuals = [item for item in surface.components if item.kind in {"fraction_explorer", "number_line"}]
        explanations = [item for item in surface.components if item.kind == "explanation"]
        questions = [item for item in surface.components if isinstance(item, QuestionCard)]
        if (
            len(visuals) != 1 or not _visual_component_matches(visuals[0], example)
            or len(explanations) != 1 or explanations[0].text != example.explanation
            or len(questions) != 1 or questions[0].question.kind != "rational"
            or questions[0].question.prompt != example.prompt
        ):
            raise studytomy_http_error(409, POLICY_NOT_APPROVED, "The learner surface does not match the reviewed lesson example.")
        expected_components = {"explanation", example.visual, "question"}
        expected_actions = {"submit_answer"}
        if source.allow_hints and example.hint:
            expected_actions.add("request_hint")
        reviewed = example
    if (
        {component.kind for component in surface.components} != expected_components
        or any(component.kind in {"hint", "feedback"} for component in surface.components)
        or set(surface.actions) != expected_actions
    ):
        raise ValueError("Initial surface components and actions must match the approved practice mode.")
    if (
        source.retention_policy_id is None or source.retention_category != "lesson_visual_practice"
        or source.retention_hours is None
    ):
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "Current D-13 retention approval is required.")
    created_at = datetime.now(UTC)
    try:
        retention_expires_at = created_at + timedelta(hours=source.retention_hours)
    except OverflowError as error:
        raise studytomy_http_error(409, POLICY_NOT_APPROVED, "Current D-13 practice retention horizon is invalid.") from error

    record = StudyLearningSurface(
        surface_uuid=str(surface.surface_id), org_id=source.org_id, user_id=viewer.id,
        request_uuid=str(surface.request_id), course_uuid=source.course_uuid,
        activity_uuid=source.activity_uuid, source_identity=source.identity,
        presentation=surface.model_dump(mode="json"),
        evaluation_basis=None if basis is None else basis.model_dump(mode="json"),
        retention_policy_id=source.retention_policy_id,
        retention_category=source.retention_category,
        retention_expires_at=retention_expires_at,
        created_at=created_at,
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
    record = await _owned_surface(db, source, viewer.id, surface_id, lock=True)
    latest = None if initial else await _latest_action(db, record.surface_uuid)
    return _project_surface(source, record, latest)


async def get_learning_availability(
    request: Request, db: AsyncSession, viewer: PublicUser, activity_uuid: str,
) -> LearningAvailability:
    source = await _authorize_source(request, db, viewer, activity_uuid)
    allow_explore = source.allow_explore and bool(
        source.approved_visuals.intersection({"fraction_explorer", "number_line"})
    )
    allow_practice = source.allow_practice and bool(source.approved_examples)
    available = allow_explore or allow_practice
    if not available:
        return LearningAvailability(available=False)
    records = (await db.execute(
        select(StudyLearningSurface).where(
            StudyLearningSurface.org_id == source.org_id,
            StudyLearningSurface.user_id == viewer.id,
            StudyLearningSurface.activity_uuid == source.activity_uuid,
            StudyLearningSurface.course_uuid == source.course_uuid,
            StudyLearningSurface.source_identity == source.identity,
            StudyLearningSurface.retention_policy_id == source.retention_policy_id,
            StudyLearningSurface.retention_category == source.retention_category,
            StudyLearningSurface.retention_expires_at > datetime.now(UTC),
        ).order_by(StudyLearningSurface.created_at.desc(), StudyLearningSurface.surface_uuid.desc())
        .limit(1).with_for_update()
    )).scalar_one_or_none()
    if records is None:
        return LearningAvailability(
            available=available, allow_explore=allow_explore, allow_practice=allow_practice,
        )
    latest = await _latest_action(db, records.surface_uuid)
    try:
        surface = _project_surface(source, records, latest)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        # A previously issued visual can lose approval while another remains available.
        return LearningAvailability(
            available=available, allow_explore=allow_explore, allow_practice=allow_practice,
        )
    return LearningAvailability(
        available=available, surface=surface,
        allow_explore=allow_explore, allow_practice=allow_practice,
    )


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
    if (
        record.retention_policy_id is None
        or record.retention_category != "lesson_visual_practice"
        or record.retention_expires_at is None
        or _as_utc(record.retention_expires_at) <= datetime.now(UTC)
    ):
        raise studytomy_http_error(410, "PRACTICE_RECORD_EXPIRED", "This saved practice has reached its approved retention limit.")
    return record


async def _latest_action(db: AsyncSession, surface_uuid: str) -> StudyLearningAction | None:
    return (await db.execute(
        select(StudyLearningAction).where(StudyLearningAction.surface_uuid == surface_uuid)
        .order_by(StudyLearningAction.revision.desc()).limit(1)
    )).scalar_one_or_none()


def _project_surface(
    source: PracticeSource, record: StudyLearningSurface, action: StudyLearningAction | None,
) -> LearningSurface:
    surface = LearningSurface.model_validate(record.presentation if action is None else action.presentation)
    mode_allowed = source.allow_explore if surface.mode == "explore" else source.allow_practice
    if not mode_allowed:
        raise studytomy_http_error(
            409, "PRACTICE_ACTION_UNAVAILABLE", "This lesson mode is not currently approved.",
        )
    if record.source_identity != source.identity:
        raise _stale_practice()
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
