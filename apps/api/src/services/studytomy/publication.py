"""Record an independent decision about a course candidate."""

import asyncio
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field as PydanticField, model_validator
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.resource_authors import (
    ResourceAuthor,
    ResourceAuthorshipEnum,
    ResourceAuthorshipStatusEnum,
)
from src.db.studytomy.education import StudyCourseRelease, StudyCourseReview
from src.db.studytomy.governance import (
    StudyOwnerFunction,
    StudyPolicyKey,
    StudyPolicyVersion,
)
from src.db.studytomy.operations import StudyEvidenceFile
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    _find_active_staff_scope,
    authorize_studytomy_staff_action,
)
from src.services.studytomy.access_policy import _validate_media_delivery_policy
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.content_evidence import (
    ContentReviewEvidenceSelection,
    CourseReviewReceipt,
    _course_scope,
    _resolve_course_review_evidence,
)
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.idempotency import (
    canonical_request_hash,
    claim_idempotency,
    complete_idempotency,
)
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization, lock_users
from src.services.studytomy.releases import (
    ReleasePolicyBinding,
    capture_native_release_media,
    course_candidate_snapshot,
    discard_retained_release_media,
    grounded_source_review_required,
    load_release_policy_binding,
    retain_reviewed_release_media,
    validate_release_media_coverage,
)

_OPERATION = "studytomy.course_review.record"
_CAPABILITY = StudytomyCapabilityName.CONTENT_RELEASE_REVIEW
_RESPONSIBILITY = StudyOwnerFunction.EDUCATION


async def _capture_review_media(org: Organization, snapshot: dict, policies: list[StudyPolicyVersion]) -> dict:
    manifest = await capture_native_release_media(org.org_uuid, snapshot)
    policy = next(policy for policy in policies if policy.policy_key == StudyPolicyKey.D_15.value)
    for item in manifest.values():
        media_type = item.get("media_type", "")
        if media_type == "text/vtt" or media_type.startswith("video/"):
            _validate_media_delivery_policy(policy, media_type, item["size_bytes"], has_captions=True)
    return manifest


class CourseReviewCommand(ContentReviewEvidenceSelection):
    decision: Literal["approved", "changes_requested"]
    accessibility_confirmed: bool
    assessment_confirmed: bool

    @model_validator(mode="after")
    def validate_findings(self):
        if self.decision == "approved" and not (
            self.accessibility_confirmed and self.assessment_confirmed
        ):
            raise ValueError("Approval requires both findings to be confirmed.")
        return self


class CourseReleasePreflight(BaseModel):
    review_uuid: str
    revision_sha256: str


class CourseReleaseReceipt(CourseReleasePreflight):
    uuid: str


async def designate_managed_course(
    db: AsyncSession, org: Organization, actor: PublicUser, course_uuid: str,
) -> None:
    await lock_organization(db, org.id)
    course, policies = await _course_scope(db, org, actor, course_uuid)
    course = (await db.execute(
        select(Course).where(Course.id == course.id, Course.org_id == org.id)
        .with_for_update().execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if course is None or course.public or course.published:
        raise studytomy_http_error(
            409, "MANAGED_COURSE_PRIVATE_DRAFT_REQUIRED",
            "Only a private, unpublished course can enter managed review.",
        )
    if not course.studytomy_managed:
        course.studytomy_managed = True
        await append_business_event(
            db, org_id=org.id, actor_user_id=actor.id,
            actor_capacity=_RESPONSIBILITY.value, subject_uuid=course_uuid,
            resource_kind="course", resource_uuid=course_uuid,
            action="studytomy.course.managed_designated",
            reason_code="COURSE_MANAGED_DESIGNATED",
            after_state={"status": "managed_draft"},
            policy_refs=[int(policy.id) for policy in policies],
            dedupe_key=f"studytomy:course:managed:{course_uuid}",
        )
    await db.commit()


async def verify_course_release(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    review_uuid: str,
) -> CourseReleasePreflight:
    course, policies = await _course_scope(db, org, actor, course_uuid)
    review, _, _ = await _checked_course_release(db, org, course, policies, review_uuid)
    return CourseReleasePreflight(
        review_uuid=review.uuid, revision_sha256=review.revision_sha256
    )


def _require_current_grounded_evidence(snapshot: dict) -> None:
    if grounded_source_review_required(snapshot):
        raise studytomy_http_error(
            409, "GROUNDED_SOURCE_REVIEW_REQUIRED",
            "A grounded draft changed or a cited source is unavailable. Update its source evidence before approval.",
        )


async def _checked_course_release(
    db: AsyncSession,
    org: Organization,
    course: Course,
    policies: list[StudyPolicyVersion],
    review_uuid: str,
) -> tuple[StudyCourseReview, dict[str, object], ReleasePolicyBinding]:
    course_uuid = course.course_uuid
    snapshot = await course_candidate_snapshot(db, course)
    _require_current_grounded_evidence(snapshot)
    digest = canonical_request_hash(snapshot)
    review = (
        await db.execute(
            select(StudyCourseReview)
            .where(
                StudyCourseReview.org_id == org.id,
                StudyCourseReview.course_uuid == course_uuid,
                StudyCourseReview.revision_sha256 == digest,
            )
            .order_by(StudyCourseReview.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    policy_ids = [int(policy.id) for policy in policies]
    if (
        review is None
        or review.uuid != review_uuid
        or review.decision != "approved"
        or review.policy_version_ids != policy_ids
        or review.media_manifest is None
    ):
        raise studytomy_http_error(
            409,
            "CURRENT_COURSE_REVIEW_REQUIRED",
            "The candidate requires a current approved review.",
        )
    binding = await load_release_policy_binding(
        db, org, datetime.now(UTC), candidate_snapshot=snapshot,
    )
    rights = await db.get(StudyEvidenceFile, review.rights_evidence_id)
    accessibility = await db.get(StudyEvidenceFile, review.accessibility_evidence_id)
    if rights is None or accessibility is None:
        raise studytomy_http_error(
            409, "REVIEW_EVIDENCE_MISMATCH", "The reviewed attachments are unavailable."
        )
    await _resolve_course_review_evidence(
        db,
        org,
        course_uuid,
        digest,
        policy_ids,
        ContentReviewEvidenceSelection(
            revision_sha256=digest,
            rights_evidence_uuid=rights.evidence_uuid,
            accessibility_evidence_uuid=accessibility.evidence_uuid,
        ),
    )
    validate_release_media_coverage(snapshot)
    if (
        await _capture_review_media(org, snapshot, policies)
        != review.media_manifest
    ):
        raise studytomy_http_error(
            409, "REVIEW_MEDIA_CHANGED", "Stored media changed after review."
        )
    return review, snapshot, binding


async def record_course_release(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    review_uuid: str,
) -> CourseReleaseReceipt:
    await lock_organization(db, org.id)
    course, policies = await _course_scope(db, org, actor, course_uuid)
    existing = (
        await db.execute(
            select(StudyCourseRelease)
            .join(
                StudyCourseReview,
                StudyCourseReview.id == StudyCourseRelease.review_id,
            )
            .where(
                StudyCourseRelease.org_id == org.id,
                StudyCourseRelease.course_uuid == course_uuid,
                StudyCourseReview.org_id == org.id,
                StudyCourseReview.course_uuid == course_uuid,
                StudyCourseReview.uuid == review_uuid,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return CourseReleaseReceipt(
            uuid=existing.uuid,
            review_uuid=review_uuid,
            revision_sha256=existing.snapshot_sha256,
        )
    review, snapshot, binding = await _checked_course_release(
        db, org, course, policies, review_uuid
    )
    versions = binding.versions
    policy_refs = [version.policy_id for version in versions.values()]
    await lock_users(db, [actor.id])
    org_uuid = org.org_uuid
    retained = await retain_reviewed_release_media(org_uuid, review.media_manifest)
    try:
        row = StudyCourseRelease(
            org_id=org.id,
            course_uuid=course_uuid,
            review_id=review.id,
            snapshot_sha256=review.revision_sha256,
            snapshot=snapshot,
            media_manifest=retained,
            assessment_policy_id=versions[StudyPolicyKey.D_08].policy_id,
            progression_policy_id=versions[StudyPolicyKey.D_09].policy_id,
            retention_policy_id=versions[StudyPolicyKey.D_13].policy_id,
            rights_policy_id=versions[StudyPolicyKey.D_16].policy_id,
            retention_category=binding.retention_category,
            policy_binding=binding.model_dump(mode="json"),
        )
        db.add(row)
        await db.flush()
        await append_business_event(
            db,
            org_id=org.id,
            actor_user_id=actor.id,
            actor_capacity=_RESPONSIBILITY.value,
            subject_uuid=course_uuid,
            resource_kind="course_release",
            resource_uuid=row.uuid,
            action="studytomy.course_release.recorded",
            reason_code="COURSE_RELEASE_RECORDED",
            after_state={"source_snapshot_digest": row.snapshot_sha256},
            policy_refs=policy_refs,
            dedupe_key=f"studytomy:course_release:{row.uuid}",
        )
        receipt = CourseReleaseReceipt(
            uuid=row.uuid,
            review_uuid=review.uuid,
            revision_sha256=row.snapshot_sha256,
        )
    except (Exception, asyncio.CancelledError):
        try:
            await db.rollback()
        finally:
            await discard_retained_release_media(org_uuid, retained)
        raise
    # Commit acknowledgment loss must preserve the retained files.
    await db.commit()
    return receipt


async def record_course_review(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    command: CourseReviewCommand,
    idempotency_key: str,
) -> CourseReviewReceipt:
    await lock_organization(db, org.id)
    course, policies = await _course_scope(db, org, actor, course_uuid)
    authors = (
        (
            await db.execute(
                select(ResourceAuthor).where(
                    ResourceAuthor.resource_uuid == course_uuid,
                    ResourceAuthor.authorship.in_(
                        (
                            ResourceAuthorshipEnum.CREATOR,
                            ResourceAuthorshipEnum.CONTRIBUTOR,
                            ResourceAuthorshipEnum.MAINTAINER,
                        )
                    ),
                    ResourceAuthor.authorship_status
                    == ResourceAuthorshipStatusEnum.ACTIVE,
                )
            )
        )
        .scalars()
        .all()
    )
    creator_ids = {
        author.user_id
        for author in authors
        if author.authorship == ResourceAuthorshipEnum.CREATOR
    }
    if len(creator_ids) != 1:
        raise studytomy_http_error(
            409,
            "COURSE_OWNER_UNRESOLVED",
            "A current course owner must be resolved before review.",
        )
    owner_id = creator_ids.pop()
    membership = (
        await db.execute(
            select(UserOrganization.id)
            .where(
                UserOrganization.org_id == org.id,
                UserOrganization.user_id == owner_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if membership is None:
        raise studytomy_http_error(
            409,
            "COURSE_OWNER_UNRESOLVED",
            "A current course owner must be resolved before review.",
        )
    now = datetime.now(UTC)
    await authorize_studytomy_staff_action(
        db,
        actor,
        org,
        _CAPABILITY,
        required_responsibility=_RESPONSIBILITY,
        target_scope_kind="course",
        target_resource_uuid=course_uuid,
        excluded_user_ids={author.user_id for author in authors},
        now=now,
    )
    assignment, allowed = await _find_active_staff_scope(
        db,
        org.id,
        actor.id,
        _RESPONSIBILITY,
        _CAPABILITY,
        "course",
        course_uuid,
        org.org_uuid,
        now,
    )
    if not allowed or assignment is None:
        raise studytomy_http_error(
            403, "COURSE_REVIEW_SCOPE_REQUIRED", "Current reviewer scope is required."
        )

    policy_ids = [int(policy.id) for policy in policies]
    await lock_users(db, [actor.id, owner_id])
    replay = await claim_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=_OPERATION,
        idempotency_key=idempotency_key,
        request_payload={
            **command.model_dump(),
            "course_uuid": course_uuid,
            "owner_user_id": owner_id,
            "reviewer_assignment_id": assignment.id,
            "policy_version_ids": policy_ids,
        },
    )
    if replay is not None:
        return CourseReviewReceipt.model_validate(replay.response_projection)

    snapshot = await course_candidate_snapshot(db, course)
    if command.decision == "approved":
        _require_current_grounded_evidence(snapshot)
        validate_release_media_coverage(snapshot)
    rights, accessibility = await _resolve_course_review_evidence(
        db,
        org,
        course_uuid,
        canonical_request_hash(snapshot),
        policy_ids,
        command,
    )
    media_manifest = (
        await _capture_review_media(org, snapshot, policies)
        if command.decision == "approved"
        else None
    )
    row = StudyCourseReview(
        org_id=org.id,
        course_uuid=course_uuid,
        revision_sha256=command.revision_sha256,
        owner_user_id=owner_id,
        reviewer_user_id=actor.id,
        reviewer_assignment_id=assignment.id,
        rights_evidence_id=rights.id,
        accessibility_evidence_id=accessibility.id,
        policy_version_ids=policy_ids,
        decision=command.decision,
        accessibility_confirmed=command.accessibility_confirmed,
        assessment_confirmed=command.assessment_confirmed,
        media_manifest=media_manifest,
    )
    db.add(row)
    await db.flush()
    await append_business_event(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        actor_capacity=_RESPONSIBILITY.value,
        subject_uuid=course_uuid,
        resource_kind="course_review",
        resource_uuid=row.uuid,
        action="studytomy.course_review.recorded",
        reason_code="COURSE_REVIEW_RECORDED",
        after_state={
            "status": row.decision,
            "source_snapshot_digest": row.revision_sha256,
        },
        policy_refs=policy_ids,
        dedupe_key=f"studytomy:course_review:{row.uuid}",
    )
    receipt = CourseReviewReceipt(
        uuid=row.uuid, revision_sha256=row.revision_sha256, decision=row.decision
    )
    await complete_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=_OPERATION,
        idempotency_key=idempotency_key,
        resource_kind="course_review",
        resource_uuid=row.uuid,
        response_status=201,
        response_projection=receipt.model_dump(),
    )
    await db.commit()
    return receipt


class PlaygroundReviewCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_digest: str = PydanticField(pattern=r"^[0-9a-f]{64}$")
    decision: Literal["approved", "rejected"]
    rights_confirmed: bool
    accessibility_confirmed: bool
    age_suitability_confirmed: bool
    evidence_reference: str = PydanticField(min_length=4, max_length=255)

    @model_validator(mode="after")
    def validate_confirmations(self):
        if self.decision == "approved" and not (
            self.rights_confirmed
            and self.accessibility_confirmed
            and self.age_suitability_confirmed
        ):
            raise ValueError(
                "Approval requires rights, accessibility, and age-suitability confirmation."
            )
        return self


class PlaygroundReviewReceipt(BaseModel):
    review_event_uuid: str
    playground_uuid: str
    decision: Literal["approved", "rejected"]
    content_digest: str


class PlaygroundReviewStatusRead(BaseModel):
    playground_uuid: str
    governed: bool
    published: bool
    deliverable: bool
    content_digest: str
    latest_decision: Literal["approved", "rejected"] | None = None
    latest_reviewed_digest: str | None = None
    current_revision_approved: bool
    can_review: bool
    is_creator: bool


def playground_content_digest(playground) -> str:
    """Canonical SHA-256 digest of all student-visible Playground state."""
    from src.services.studytomy.idempotency import canonical_request_hash

    access = getattr(playground, "access_type", None)
    if hasattr(access, "value"):
        access = access.value
    reviewed_state = {
        "schema": "studytomy_playground_review_v1",
        "name": getattr(playground, "name", ""),
        "description": getattr(playground, "description", None),
        "access_type": access,
        "course_uuid": getattr(playground, "course_uuid", None),
        "html_content": getattr(playground, "html_content", None),
        "learner_adaptation": getattr(playground, "learner_adaptation", None),
    }
    thumbnail_name = getattr(playground, "thumbnail_image", None)
    thumbnail_sha256 = getattr(playground, "thumbnail_content_sha256", None)
    if thumbnail_name or thumbnail_sha256:
        # Keep the stored reference and byte identity together. A reference by
        # itself is mutable and cannot prove which image the reviewer saw.
        reviewed_state["thumbnail"] = {
            "stored_name": thumbnail_name,
            "content_sha256": thumbnail_sha256,
        }
    return canonical_request_hash(reviewed_state)


async def is_studytomy_governed_playground(
    db: AsyncSession,
    playground,
) -> bool:
    """True when this Playground belongs to a Studytomy school/minors cohort.

    Plain Studytomy Portal Community organizations without Studytomy learner profiles,
    approved Studytomy policies, or managed-course links keep upstream behavior.
    """
    from src.db.studytomy.family import StudyLearnerProfile
    from src.db.studytomy.governance import StudyPolicyStatus, StudyPolicyVersion

    if getattr(playground, "course_id", None) is not None:
        managed_course = (
            await db.execute(
                select(Course.id).where(
                    Course.id == playground.course_id,
                    Course.org_id == playground.org_id,
                    Course.studytomy_managed.is_(True),
                )
            )
        ).scalar_one_or_none()
        if managed_course is not None:
            return True

    has_profile = (
        await db.execute(
            select(StudyLearnerProfile.id)
            .where(StudyLearnerProfile.org_id == playground.org_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if has_profile is not None:
        return True

    has_policy = (
        await db.execute(
            select(StudyPolicyVersion.id)
            .where(
                StudyPolicyVersion.org_id == playground.org_id,
                StudyPolicyVersion.status == StudyPolicyStatus.APPROVED.value,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return has_policy is not None


async def _latest_playground_review_event(
    db: AsyncSession,
    playground,
):
    from src.db.studytomy.governance import StudyBusinessEvent

    return (
        await db.execute(
            select(StudyBusinessEvent)
            .where(
                StudyBusinessEvent.org_id == playground.org_id,
                StudyBusinessEvent.resource_kind == "playground",
                StudyBusinessEvent.resource_uuid == playground.playground_uuid,
                StudyBusinessEvent.action.in_(
                    (
                        "studytomy.playground.review_approved",
                        "studytomy.playground.review_rejected",
                    )
                ),
            )
            .order_by(StudyBusinessEvent.created_at.desc(), StudyBusinessEvent.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def has_approved_playground_review(
    db: AsyncSession,
    playground,
    content_digest: str | None = None,
) -> bool:
    """Return True only if an independent reviewer approved the exact current digest."""
    expected = content_digest or playground_content_digest(playground)
    event = await _latest_playground_review_event(db, playground)
    if event is None or event.action != "studytomy.playground.review_approved":
        return False
    if playground.created_by is not None and event.actor_user_id == playground.created_by:
        return False
    state = event.after_state if isinstance(event.after_state, dict) else {}
    return state.get("status") == "approved" and state.get("evidence_sha256") == expected


async def is_playground_deliverable_to_learners(
    db: AsyncSession,
    playground,
    *,
    governed: bool | None = None,
) -> bool:
    """Single canonical gate for learner visibility & delivery of a Playground."""
    if not getattr(playground, "published", False):
        return False
    is_governed = (
        governed
        if governed is not None
        else await is_studytomy_governed_playground(db, playground)
    )
    if not is_governed:
        return True
    return await has_approved_playground_review(db, playground)


async def can_actor_review_playground(
    db: AsyncSession,
    playground,
    actor,
    *,
    organization: Organization | None = None,
    now: datetime | None = None,
) -> bool:
    """True only when actor holds valid independent review authority for this Playground."""
    from src.db.users import AnonymousUser, APITokenUser

    if isinstance(actor, (AnonymousUser, APITokenUser)) or getattr(actor, "id", None) is None:
        return False
    org = organization
    if org is None:
        org = (
            await db.execute(
                select(Organization).where(Organization.id == playground.org_id)
            )
        ).scalar_one_or_none()
    if org is None:
        return False
    excluded = {int(playground.created_by)} if playground.created_by is not None else set()
    scope_kind = "course" if getattr(playground, "course_uuid", None) else "function"
    scope_uuid = getattr(playground, "course_uuid", None) or _RESPONSIBILITY.value
    try:
        await authorize_studytomy_staff_action(
            db,
            actor,
            org,
            StudytomyCapabilityName.CONTENT_RELEASE_REVIEW,
            target_scope_kind=scope_kind,
            target_resource_uuid=scope_uuid,
            excluded_user_ids=excluded,
            hide_existence_on_denial=True,
            now=now or datetime.now(UTC),
        )
        return True
    except HTTPException:
        return False


async def get_playground_review_status(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    playground_uuid: str,
) -> PlaygroundReviewStatusRead:
    """Return narrow governance/review metadata for an authorized editor, admin, or reviewer."""
    from src.db.playgrounds import Playground
    from src.services.playgrounds.playgrounds import _get_user_rights, _is_org_admin

    now = datetime.now(UTC)
    playground = (
        await db.execute(
            select(Playground).where(
                Playground.org_id == org.id,
                Playground.playground_uuid == playground_uuid,
            )
        )
    ).scalar_one_or_none()
    if playground is None:
        raise HTTPException(404, "Playground not found.")

    acting_user_id = int(actor.id)
    is_creator = playground.created_by == acting_user_id
    is_admin = await _is_org_admin(acting_user_id, org.id, db)
    rights = await _get_user_rights(acting_user_id, org.id, db)
    pg_rights = rights.get("playgrounds", {})
    can_edit = bool(
        pg_rights.get("action_update")
        or (is_creator and pg_rights.get("action_update_own"))
    )
    can_review = await can_actor_review_playground(
        db, playground, actor, organization=org, now=now
    )
    if not (is_creator or is_admin or can_edit or can_review):
        raise HTTPException(404, "Playground not found.")

    governed = await is_studytomy_governed_playground(db, playground)
    digest = playground_content_digest(playground)
    event = await _latest_playground_review_event(db, playground)
    latest_decision: Literal["approved", "rejected"] | None = None
    latest_reviewed_digest: str | None = None
    current_revision_approved = False
    if event is not None:
        state = event.after_state if isinstance(event.after_state, dict) else {}
        if event.action == "studytomy.playground.review_approved":
            latest_decision = "approved"
        elif event.action == "studytomy.playground.review_rejected":
            latest_decision = "rejected"
        raw_digest = state.get("evidence_sha256")
        if isinstance(raw_digest, str):
            latest_reviewed_digest = raw_digest
        not_self = playground.created_by is None or event.actor_user_id != playground.created_by
        current_revision_approved = (
            latest_decision == "approved"
            and state.get("status") == "approved"
            and latest_reviewed_digest == digest
            and not_self
        )
    deliverable = bool(playground.published) and (not governed or current_revision_approved)
    return PlaygroundReviewStatusRead(
        playground_uuid=playground.playground_uuid,
        governed=governed,
        published=bool(playground.published),
        deliverable=deliverable,
        content_digest=digest,
        latest_decision=latest_decision,
        latest_reviewed_digest=latest_reviewed_digest,
        current_revision_approved=current_revision_approved,
        can_review=can_review,
        is_creator=is_creator,
    )


async def record_playground_review(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    playground_uuid: str,
    command: PlaygroundReviewCommand,
    idempotency_key: str,
) -> PlaygroundReviewReceipt:
    """Record an independent reviewer's digest-bound decision for a Playground."""
    from src.db.playgrounds import Playground

    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    playground = (
        await db.execute(
            select(Playground)
            .where(
                Playground.org_id == org.id,
                Playground.playground_uuid == playground_uuid,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if playground is None:
        raise HTTPException(404, "Playground not found.")

    excluded = {int(playground.created_by)} if playground.created_by is not None else set()
    scope_kind = "course" if playground.course_uuid else "function"
    scope_uuid = playground.course_uuid or _RESPONSIBILITY.value
    await authorize_studytomy_staff_action(
        db,
        actor,
        org,
        StudytomyCapabilityName.CONTENT_RELEASE_REVIEW,
        target_scope_kind=scope_kind,
        target_resource_uuid=scope_uuid,
        excluded_user_ids=excluded,
        hide_existence_on_denial=True,
        now=now,
    )
    policies = await _current_policies(
        db, org, (StudyPolicyKey.D_15, StudyPolicyKey.D_16), now
    )
    policy_ids = sorted(int(p.id) for p in policies)

    operation = "studytomy.playground.review"
    replay = await claim_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_payload={
            "playground_uuid": playground_uuid,
            "command": command.model_dump(mode="json"),
        },
    )
    if replay is not None:
        return PlaygroundReviewReceipt.model_validate(replay.response_projection)

    current_digest = playground_content_digest(playground)
    if command.expected_digest != current_digest:
        raise studytomy_http_error(
            409,
            "PLAYGROUND_REVIEW_STALE",
            "The Playground changed since inspection. Reload and review the current revision.",
        )

    thumbnail_name = getattr(playground, "thumbnail_image", None)
    thumbnail_sha256 = getattr(playground, "thumbnail_content_sha256", None)
    if command.decision == "approved" and (thumbnail_name or thumbnail_sha256):
        valid_thumbnail_digest = (
            isinstance(thumbnail_sha256, str)
            and len(thumbnail_sha256) == 64
            and all(char in "0123456789abcdef" for char in thumbnail_sha256)
        )
        if not thumbnail_name or not valid_thumbnail_digest:
            raise studytomy_http_error(
                409,
                "PLAYGROUND_THUMBNAIL_IDENTITY_REQUIRED",
                "Upload the thumbnail through the Playground thumbnail endpoint before approving this revision.",
            )

    action = (
        "studytomy.playground.review_approved"
        if command.decision == "approved"
        else "studytomy.playground.review_rejected"
    )
    reason = (
        "PLAYGROUND_REVIEW_APPROVED"
        if command.decision == "approved"
        else "PLAYGROUND_REVIEW_REJECTED"
    )
    event = await append_business_event(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        actor_capacity=_RESPONSIBILITY.value,
        subject_uuid=playground.playground_uuid,
        resource_kind="playground",
        resource_uuid=playground.playground_uuid,
        action=action,
        reason_code=reason,
        after_state={
            "status": command.decision,
            "evidence_sha256": current_digest,
            "evidence_reference": command.evidence_reference,
        },
        policy_refs=policy_ids,
        dedupe_key=f"studytomy:playground_review:{playground.playground_uuid}:{current_digest}:{idempotency_key}",
    )
    if command.decision == "rejected" and playground.published:
        # Atomically withdraw rejected Playground content so learners cannot read
        # or list previously published revisions after rejection.
        playground.published = False
        playground.update_date = now.replace(tzinfo=None).isoformat()
        db.add(playground)
    receipt = PlaygroundReviewReceipt(
        review_event_uuid=event.event_uuid,
        playground_uuid=playground.playground_uuid,
        decision=command.decision,
        content_digest=current_digest,
    )
    await complete_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=operation,
        idempotency_key=idempotency_key,
        resource_kind="playground_review",
        resource_uuid=event.event_uuid,
        response_status=201,
        response_projection=receipt.model_dump(),
    )
    await db.commit()
    return receipt
