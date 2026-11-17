"""Private course evidence bound to the server's current content fingerprint."""

import asyncio
import logging
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.education import StudyCourseReview
from src.db.studytomy.governance import (
    StudyOwnerFunction,
    StudyPolicyKey,
    StudyPolicyVersion,
)
from src.db.studytomy.operations import StudyEvidenceFile
from src.db.users import PublicUser
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_studytomy_staff_action,
)
from src.services.courses.transfer.storage_utils import delete_storage_file
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.errors import STALE_VERSION, studytomy_http_error
from src.services.studytomy.evidence_files import (
    _SUPPORTED_MEDIA,
    _approved_file_rules,
    _open_private_evidence,
    _validated_evidence_file,
    _verify_private_evidence,
)
from src.services.studytomy.idempotency import (
    canonical_request_hash,
    claim_idempotency,
    complete_idempotency,
)
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization, lock_users
from src.services.studytomy.releases import course_candidate_snapshot, grounded_source_review_required
from src.services.utils.upload_content import upload_content

ContentEvidencePurpose = Literal["content_rights", "content_accessibility"]
_POLICIES = (
    StudyPolicyKey.D_02, StudyPolicyKey.D_10, StudyPolicyKey.D_12,
    StudyPolicyKey.D_13, StudyPolicyKey.D_15, StudyPolicyKey.D_16,
)
_EVIDENCE_PURPOSES = ("content_rights", "content_accessibility")
_OPERATION = "studytomy.content_evidence.upload"
logger = logging.getLogger(__name__)


class ContentEvidenceRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_uuid: str
    course_uuid: str
    revision_sha256: str
    purpose: ContentEvidencePurpose
    content_sha256: str
    media_type: str
    size_bytes: int
    verified_at: datetime

    @field_validator("verified_at")
    @classmethod
    def normalize_verified_at(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class CourseReviewReceipt(BaseModel):
    uuid: str
    revision_sha256: str
    decision: str


class ContentReviewCandidate(BaseModel):
    revision_sha256: str
    evidence: list[ContentEvidenceRead]
    next_before_id: int | None = None
    current_review: CourseReviewReceipt | None = None
    grounding_review_required: bool = False


class ContentReviewEvidenceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rights_evidence_uuid: str = Field(pattern=r"^ev_[0-9a-f-]{36}$")
    accessibility_evidence_uuid: str = Field(pattern=r"^ev_[0-9a-f-]{36}$")


def _projection(record: StudyEvidenceFile) -> ContentEvidenceRead:
    return ContentEvidenceRead.model_validate(record, from_attributes=True)


async def _course_scope(
    db: AsyncSession, organization: Organization, actor: PublicUser, course_uuid: str,
) -> tuple[Course, list[StudyPolicyVersion]]:
    await authorize_studytomy_staff_action(
        db, actor, organization, StudytomyCapabilityName.CONTENT_RELEASE_REVIEW,
        target_scope_kind="course", target_resource_uuid=course_uuid,
    )
    course = (await db.execute(select(Course).where(
        Course.org_id == organization.id, Course.course_uuid == course_uuid,
    ))).scalar_one_or_none()
    if course is None:
        raise HTTPException(404, "Course not found")
    policies = await _current_policies(db, organization, _POLICIES, datetime.now(UTC))
    return course, policies


async def _candidate_fingerprint(db: AsyncSession, course: Course) -> str:
    return canonical_request_hash(await course_candidate_snapshot(db, course))


async def get_course_content_candidate(
    db: AsyncSession, organization: Organization, actor: PublicUser, course_uuid: str,
    *, before_id: int | None = None,
) -> ContentReviewCandidate:
    course, policies = await _course_scope(db, organization, actor, course_uuid)
    snapshot = await course_candidate_snapshot(db, course)
    revision = canonical_request_hash(snapshot)
    policy_ids = [int(policy.id) for policy in policies]
    query = select(StudyEvidenceFile).where(
        StudyEvidenceFile.org_id == organization.id,
        StudyEvidenceFile.course_uuid == course_uuid,
        StudyEvidenceFile.revision_sha256 == revision,
        StudyEvidenceFile.purpose.in_(_EVIDENCE_PURPOSES),
        StudyEvidenceFile.upload_status == "ready",
        StudyEvidenceFile.verified_at.is_not(None),
    )
    if before_id is not None:
        query = query.where(StudyEvidenceFile.id < before_id)
    page_size = 25
    rows = (await db.execute(
        query.order_by(StudyEvidenceFile.id.desc()).limit(page_size + 1)
    )).scalars().all()
    page = rows[:page_size]
    latest_review = (await db.execute(
        select(StudyCourseReview).where(
            StudyCourseReview.org_id == organization.id,
            StudyCourseReview.course_uuid == course_uuid,
            StudyCourseReview.revision_sha256 == revision,
        ).order_by(StudyCourseReview.id.desc()).limit(1)
    )).scalar_one_or_none()
    return ContentReviewCandidate(
        revision_sha256=revision,
        grounding_review_required=grounded_source_review_required(snapshot),
        evidence=[_projection(row) for row in page if row.policy_version_ids == policy_ids],
        next_before_id=page[-1].id if len(rows) > page_size else None,
        current_review=(
            CourseReviewReceipt.model_validate(latest_review, from_attributes=True)
            if latest_review is not None and latest_review.policy_version_ids == policy_ids
            else None
        ),
    )


async def _course_evidence(db, organization, course_uuid, evidence_uuid) -> StudyEvidenceFile:
    record = (await db.execute(select(StudyEvidenceFile).where(
        StudyEvidenceFile.org_id == organization.id, StudyEvidenceFile.course_uuid == course_uuid,
        StudyEvidenceFile.evidence_uuid == evidence_uuid,
        StudyEvidenceFile.purpose.in_(_EVIDENCE_PURPOSES), StudyEvidenceFile.upload_status == "ready",
    ))).scalar_one_or_none()
    if record is None:
        raise HTTPException(404, "Course evidence not found")
    return record


async def verify_course_review_evidence(
    db: AsyncSession, organization: Organization, actor: PublicUser, course_uuid: str,
    selection: ContentReviewEvidenceSelection,
) -> list[ContentEvidenceRead]:
    course, policies = await _course_scope(db, organization, actor, course_uuid)
    revision = await _candidate_fingerprint(db, course)
    records = await _resolve_course_review_evidence(
        db, organization, course_uuid, revision, [int(policy.id) for policy in policies], selection
    )
    return [_projection(record) for record in records]


async def _resolve_course_review_evidence(
    db: AsyncSession, organization: Organization, course_uuid: str, revision: str,
    policy_ids: list[int], selection: ContentReviewEvidenceSelection,
) -> list[StudyEvidenceFile]:
    if selection.revision_sha256 != revision:
        raise studytomy_http_error(409, STALE_VERSION, "Course content has changed. Reload the review candidate.")
    records = []
    for evidence_uuid, purpose in (
        (selection.rights_evidence_uuid, "content_rights"),
        (selection.accessibility_evidence_uuid, "content_accessibility"),
    ):
        record = await _course_evidence(db, organization, course_uuid, evidence_uuid)
        if (
            record.purpose != purpose
            or record.revision_sha256 != revision
            or record.policy_version_ids != policy_ids
            or record.verified_at is None
        ):
            raise studytomy_http_error(
                409, "REVIEW_EVIDENCE_MISMATCH",
                "The attachment does not match the current candidate, purpose or policies.",
            )
        records.append(record)
    for record in records:
        await _verify_private_evidence(organization, record)
    return records


async def upload_course_evidence(
    db: AsyncSession, organization: Organization, actor: PublicUser, course_uuid: str,
    revision_sha256: str, purpose: ContentEvidencePurpose, file: UploadFile, idempotency_key: str,
) -> ContentEvidenceRead:
    await lock_organization(db, organization.id)
    course, policies = await _course_scope(db, organization, actor, course_uuid)
    max_bytes, formats, retention = _approved_file_rules(
        policies, retention_field="content_evidence_retention_category",
    )
    if revision_sha256 != await _candidate_fingerprint(db, course):
        raise studytomy_http_error(409, STALE_VERSION, "Course content has changed. Reload the review candidate.")
    digest, size, media_type = await _validated_evidence_file(file, max_bytes, formats)
    policy_ids = [int(policy.id) for policy in policies]
    await lock_users(db, [actor.id])
    replay = await claim_idempotency(
        db, org_id=organization.id, actor_user_id=actor.id, operation=_OPERATION,
        idempotency_key=idempotency_key,
        request_payload={
            "course_uuid": course_uuid, "revision_sha256": revision_sha256, "purpose": purpose,
            "content_sha256": digest, "size": size, "media_type": media_type, "policy_ids": policy_ids,
        },
    )
    if replay is not None:
        return _projection(await _course_evidence(db, organization, course_uuid, replay.resource_uuid))

    evidence_uuid = f"ev_{uuid4()}"
    filename = f"{evidence_uuid}{_SUPPORTED_MEDIA[media_type][1]}"
    storage = f"content/orgs/{organization.org_uuid}/studytomy/evidence/{filename}"
    commit_started = False
    try:
        await upload_content(
            directory="studytomy/evidence", type_of_dir="orgs", uuid=organization.org_uuid,
            file_binary=file.file, file_and_format=filename, allowed_formats=None,
        )
        record = StudyEvidenceFile(
            evidence_uuid=evidence_uuid, org_id=organization.id, course_uuid=course_uuid,
            revision_sha256=revision_sha256, purpose=purpose, storage_reference=storage,
            content_sha256=digest, size_bytes=size, media_type=media_type, retention_category=retention,
            policy_version_ids=policy_ids, uploaded_by_user_id=actor.id, upload_status="ready",
        )
        await _verify_private_evidence(organization, record)
        record.verified_at = datetime.now(UTC)
        db.add(record)
        await db.flush()
        await append_business_event(
            db, org_id=organization.id, actor_user_id=actor.id, actor_capacity=StudyOwnerFunction.EDUCATION.value,
            subject_uuid=course_uuid, resource_kind="evidence_file", resource_uuid=evidence_uuid,
            action="studytomy.content_evidence.uploaded", reason_code="EVIDENCE_FILE_UPLOADED",
            after_state={"status": "ready", "resource_kind": purpose}, policy_refs=policy_ids,
            dedupe_key=f"studytomy:evidence:{evidence_uuid}:uploaded",
        )
        await complete_idempotency(
            db, org_id=organization.id, actor_user_id=actor.id, operation=_OPERATION,
            idempotency_key=idempotency_key, resource_kind="evidence_file",
            resource_uuid=evidence_uuid, response_status=201,
        )
        result = _projection(record)
        commit_started = True
        await db.commit()
    except BaseException:
        await db.rollback()
        if not commit_started:
            try:
                await asyncio.to_thread(delete_storage_file, storage)
            except Exception:  # noqa: BLE001 - preserve the original error without exposing the storage key
                logger.warning("Course evidence upload cleanup failed")
        raise
    return result


async def open_course_evidence(db, organization, actor, course_uuid, evidence_uuid):
    _, policies = await _course_scope(db, organization, actor, course_uuid)
    record = await _course_evidence(db, organization, course_uuid, evidence_uuid)
    if record.policy_version_ids != [int(policy.id) for policy in policies]:
        raise studytomy_http_error(409, "EVIDENCE_POLICY_STALE", "Current content evidence policies require a review.")
    return _projection(record), await _open_private_evidence(organization, record)
