"""Record an independent decision about a course candidate."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, model_validator
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
    binding = await load_release_policy_binding(db, org, datetime.now(UTC))
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
        await capture_native_release_media(org.org_uuid, snapshot)
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
    except Exception:
        await db.rollback()
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
    rights, accessibility = await _resolve_course_review_evidence(
        db,
        org,
        course_uuid,
        canonical_request_hash(snapshot),
        policy_ids,
        command,
    )
    media_manifest = (
        await capture_native_release_media(org.org_uuid, snapshot)
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
