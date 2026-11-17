"""Resolve current learner and source authority for diagnostic attempts."""

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime

from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import Activity
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.adaptive import (
    StudyConcept,
    StudyConceptActivity,
    StudyCurriculumRevision,
)
from src.db.studytomy.adaptive_diagnostics import StudyDiagnosticRevision
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import StudyPolicyKey
from src.db.studytomy.privacy import StudyConsentPurpose
from src.db.users import PublicUser
from src.security.rbac import AccessAction
from src.services.ai.rag.access import can_read_activity
from src.services.courses.activities.assignments import authorize_assignment_access
from src.services.studytomy.access_policy import (
    ManagedReleaseAuthorization,
    _project_media_activity,
    _project_text_lesson,
    _unavailable,
    resolve_managed_release_authority,
)
from src.services.studytomy.adaptive_access import (
    _LEARNING_POLICIES,
    _is_learning_eligible,
    adaptive_enabled,
)
from src.services.studytomy.adaptive_curriculum import (
    curriculum_activity_digest,
    curriculum_activity_snapshot_digest,
)
from src.services.studytomy.adaptive_curriculum_review import CurriculumPublish
from src.services.studytomy.adaptive_diagnostic_selection import (
    SELECTOR_VERSION,
    DiagnosticManifest,
)
from src.services.studytomy.adaptive_diagnostics import (
    _POLICIES,
    _manifest,
    _policy_body,
    _require_exact_approval,
)
from src.services.studytomy.adaptive_mastery import (
    MasteryPolicy,
    resolve_mastery_policy,
)
from src.services.studytomy.consents import has_current_consent_scope
from src.services.studytomy.entitlements import resolve_active_course_grants
from src.services.studytomy.errors import (
    POLICY_NOT_APPROVED,
    STALE_VERSION,
    studytomy_http_error,
)
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization

DIAGNOSTIC_SCOPE = "adaptive_diagnostics"
PRACTICE_SCOPE = "adaptive_practice"
_ATTEMPT_POLICIES = tuple(
    dict.fromkeys(
        (
            *_LEARNING_POLICIES,
            *_POLICIES,
            StudyPolicyKey.D_03,
            StudyPolicyKey.D_05,
            StudyPolicyKey.D_18,
        )
    )
)


@dataclass(frozen=True, slots=True)
class DiagnosticAccess:
    profile: StudyLearnerProfile
    definition: StudyDiagnosticRevision
    curriculum: StudyCurriculumRevision
    manifest: DiagnosticManifest
    policy: MasteryPolicy
    policy_ids: list[int]
    activity_uuid: str

    @property
    def available(self) -> bool:
        return (
            self.definition.status == "published"
            and self.curriculum.status == "published"
        )


@dataclass(frozen=True, slots=True)
class ResolvedManagedReleaseSource:
    """Exact released activity plus the authority used to resolve it."""

    authorization: ManagedReleaseAuthorization
    curriculum: StudyCurriculumRevision
    activity_uuid: str
    activity_version: int
    activity_digest: str
    activity_snapshot: dict[str, object]


async def lock_attempt_learner(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    now: datetime,
    scope: str,
) -> StudyLearnerProfile:
    if not await adaptive_enabled(db, org):
        raise studytomy_http_error(
            403, "ADAPTIVE_DISABLED", "Adaptive learning is unavailable."
        )
    profile = (
        await db.execute(
            select(StudyLearnerProfile)
            .where(
                StudyLearnerProfile.org_id == org.id,
                StudyLearnerProfile.user_id == actor.id,
                StudyLearnerProfile.status == "active",
                StudyLearnerProfile.control_mode == "independent",
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(404, "Learning record not found.")
    if not await _is_learning_eligible(
        db, org, profile, now
    ) or not await has_current_consent_scope(
        db,
        org,
        profile,
        StudyConsentPurpose.LEARNING_TERMS,
        scope,
        now,
    ):
        raise studytomy_http_error(
            403,
            POLICY_NOT_APPROVED,
            "Current learner approval and consent for this activity are required.",
        )
    return profile


async def require_mapped_release_source(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    profile: StudyLearnerProfile,
    curriculum: StudyCurriculumRevision,
    concept_uuids: tuple[str, ...],
    course_uuid: str,
    release_uuid: str,
    activity_uuid: str,
    now: datetime,
    *,
    for_practice: bool = False,
    claim_first_use: bool = False,
) -> ResolvedManagedReleaseSource:
    """Resolve an adaptive source from its authorized immutable course release.

    Native non-managed sources continue through ``require_mapped_source``.
    Managed-course authoring rows are never returned by this branch.
    """
    if (
        profile.org_id != org.id
        or profile.user_id != actor.id
        or profile.status != "active"
        or profile.control_mode != "independent"
        or curriculum.org_id != org.id
        or curriculum.status not in {"published", "retired"}
        or curriculum.owner_course_uuid != course_uuid
        or not curriculum.content_digest
        or not concept_uuids
    ):
        raise _unavailable()

    # Serialize this authorization/read operation with grant and policy changes;
    # no external storage I/O is performed while this lock is held.
    await lock_organization(db, org.id)
    authorization = await resolve_managed_release_authority(
        db,
        org,
        actor,
        course_uuid,
        release_uuid,
        now=now,
        include_unstarted=claim_first_use,
    )
    if authorization.profile.id != profile.id:
        raise _unavailable()

    snapshot = authorization.release.snapshot
    if snapshot.get("schema") != "course_content_evidence_v4":
        raise _unavailable()
    activity_rows = snapshot.get("activities")
    activities = (
        [
            row
            for row in activity_rows
            if isinstance(row, dict) and row.get("activity_uuid") == activity_uuid
        ]
        if isinstance(activity_rows, list)
        else []
    )
    if len(activities) != 1:
        raise _unavailable()
    activity = activities[0]
    activity_id = activity.get("id")
    activity_version = activity.get("current_version")
    if (
        type(activity_id) is not int
        or activity_id < 1
        or type(activity_version) is not int
        or activity_version < 1
        or activity.get("published") is not True
    ):
        raise _unavailable()
    try:
        activity_digest = curriculum_activity_snapshot_digest(activity)
    except (TypeError, ValueError):
        raise _unavailable() from None

    mapping_query = (
        select(StudyConceptActivity.id)
        .join(StudyConcept, StudyConcept.id == StudyConceptActivity.concept_id)
        .where(
            StudyConceptActivity.org_id == org.id,
            StudyConceptActivity.curriculum_revision_id == curriculum.id,
            StudyConceptActivity.course_id == authorization.course_id,
            StudyConceptActivity.course_uuid == course_uuid,
            StudyConceptActivity.activity_id == activity_id,
            StudyConceptActivity.activity_uuid == activity_uuid,
            StudyConceptActivity.activity_version == activity_version,
            StudyConceptActivity.activity_digest == activity_digest,
            StudyConceptActivity.archived.is_(False),
            StudyConcept.uuid.in_(concept_uuids),
            StudyConcept.archived.is_(False),
        )
    )
    if for_practice:
        mapping_query = mapping_query.where(StudyConceptActivity.usage == "practice")
    if await db.scalar(mapping_query.limit(1)) is None:
        raise _unavailable()

    # The current release projector is the authority for what this learner can
    # actually open. Unsupported/native-only formats stay unavailable here.
    try:
        subtype = activity.get("activity_sub_type")
        if subtype == "SUBTYPE_DYNAMIC_PAGE":
            _project_text_lesson(
                authorization.release,
                authorization.progression_policy,
                activity_uuid,
            )
        elif subtype in {"SUBTYPE_DOCUMENT_PDF", "SUBTYPE_VIDEO_HOSTED"}:
            _project_media_activity(
                authorization.release,
                authorization.progression_policy,
                activity_uuid,
            )
        else:
            raise _unavailable()
    except HTTPException as error:
        if error.status_code == 404:
            raise _unavailable() from error
        raise

    if claim_first_use:
        grants = tuple(
            await resolve_active_course_grants(
                db,
                org_id=org.id,
                learner_profile_id=profile.id,
                course_uuid=course_uuid,
                release_id=authorization.release.id,
                now=now,
                claim_first_use=True,
            )
        )
        if not grants:
            raise _unavailable()
        authorization = replace(authorization, grants=grants)

    return ResolvedManagedReleaseSource(
        authorization=authorization,
        curriculum=curriculum,
        activity_uuid=activity_uuid,
        activity_version=activity_version,
        activity_digest=activity_digest,
        activity_snapshot=deepcopy(activity),
    )


async def require_mapped_source(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    curriculum: StudyCurriculumRevision,
    concept_uuids: tuple[str, ...],
    activity_uuid: str,
    *,
    for_practice: bool = False,
) -> Activity:
    pairs = (
        await db.execute(
            select(Activity, Course)
            .join(Course, Course.id == Activity.course_id)
            .where(
                Activity.activity_uuid == activity_uuid,
                Activity.org_id == org.id,
                Course.org_id == org.id,
                Course.course_uuid == curriculum.owner_course_uuid,
                Course.published.is_(True),
                Activity.published.is_(True),
            )
            .limit(2)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    ).all()
    if len(pairs) != 1:
        raise HTTPException(404, "Learning source not found.")
    activity, course = pairs[0]
    mappings = (
        await db.execute(
            select(StudyConceptActivity)
            .join(
                StudyConcept,
                StudyConcept.id == StudyConceptActivity.concept_id,
            )
            .where(
                StudyConceptActivity.org_id == org.id,
                StudyConceptActivity.curriculum_revision_id == curriculum.id,
                StudyConceptActivity.course_id == course.id,
                StudyConceptActivity.course_uuid == course.course_uuid,
                StudyConceptActivity.activity_id == activity.id,
                StudyConceptActivity.activity_uuid == activity_uuid,
                StudyConceptActivity.activity_version == activity.current_version,
                StudyConceptActivity.archived.is_(False),
                StudyConcept.uuid.in_(concept_uuids),
                StudyConcept.archived.is_(False),
                *((StudyConceptActivity.usage == "practice",) if for_practice else ()),
            )
        )
    ).scalars()
    digest = curriculum_activity_digest(activity)
    if not any(mapping.activity_digest == digest for mapping in mappings):
        raise HTTPException(404, "Learning source not found.")
    if for_practice:
        await authorize_assignment_access(
            request,
            db,
            actor,
            course.course_uuid,
            AccessAction.READ,
            activity_id=activity.id,
        )
    elif not await can_read_activity(request, actor, course, activity.id, db):
        raise HTTPException(404, "Learning source not found.")
    return activity


async def require_diagnostic_access(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    profile: StudyLearnerProfile,
    definition_uuid: str,
    activity_uuid: str,
    now: datetime,
    *,
    validate_pool: bool,
) -> DiagnosticAccess:
    pair = (
        await db.execute(
            select(StudyDiagnosticRevision, StudyCurriculumRevision)
            .join(
                StudyCurriculumRevision,
                StudyCurriculumRevision.id
                == StudyDiagnosticRevision.curriculum_revision_id,
            )
            .where(
                StudyDiagnosticRevision.org_id == org.id,
                StudyDiagnosticRevision.uuid == definition_uuid,
                StudyDiagnosticRevision.status.in_(("published", "retired")),
            )
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    ).one_or_none()
    if pair is None:
        raise HTTPException(404, "Diagnostic not found.")
    definition, curriculum = pair
    manifest = DiagnosticManifest.model_validate(definition.manifest)
    await require_mapped_source(
        request, db, org, actor, curriculum, manifest.required_concepts, activity_uuid
    )
    policies = await _current_policies(db, org, _ATTEMPT_POLICIES, now)
    d16 = _policy_body(policies, StudyPolicyKey.D_16).specified_value(
        "review_approvals"
    )
    entries = d16.get("adaptive_diagnostic_reviews") if isinstance(d16, dict) else None
    reference = (
        next(
            (
                entry.get("evidence_reference")
                for entry in entries
                if isinstance(entry, dict)
                and entry.get("revision_uuid") == definition.uuid
                and entry.get("digest") == definition.content_digest
            ),
            None,
        )
        if isinstance(entries, list)
        else None
    )
    try:
        exact = CurriculumPublish(
            expected_version=definition.version,
            expected_digest=definition.content_digest,
            review_evidence=reference,
        )
    except ValidationError as error:
        raise studytomy_http_error(
            409, POLICY_NOT_APPROVED, "Current diagnostic approval is required."
        ) from error
    _require_exact_approval(policies, definition, exact)
    policy = resolve_mastery_policy(
        _policy_body(policies, StudyPolicyKey.D_09), definition.mastery_policy_uuid
    )
    if (
        definition.selector_version != SELECTOR_VERSION
        or policy.digest != manifest.mastery_policy_digest
    ):
        raise studytomy_http_error(
            409, STALE_VERSION, "The diagnostic policy has changed."
        )
    access = DiagnosticAccess(
        profile,
        definition,
        curriculum,
        manifest,
        policy,
        [int(p.id) for p in policies],
        activity_uuid,
    )
    if validate_pool and access.available:
        current = await _manifest(
            db,
            curriculum,
            policies,
            manifest.required_concepts,
            tuple(item.uuid for item in manifest.pool),
            manifest.limits,
            manifest.rules,
            definition.mastery_policy_uuid,
        )
        if current != manifest:
            raise studytomy_http_error(
                409, STALE_VERSION, "The reviewed diagnostic item pool has changed."
            )
    return access
