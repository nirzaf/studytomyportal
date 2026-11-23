"""Self-service access to reviewed managed course releases."""

import asyncio
import hashlib
import re
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from tempfile import TemporaryFile
from types import SimpleNamespace
from typing import Any, BinaryIO, Literal

from fastapi import HTTPException, UploadFile
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import Select, and_, case, or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.access import StudyCourseAccessGrant
from src.db.studytomy.education import StudyCourseRelease, StudyCourseReview
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyPolicyKey,
    StudyPolicyVersion,
    inspect_study_policy_body,
)
from src.db.studytomy.privacy import StudyConsentPurpose
from src.db.trail_runs import StatusEnum, TrailRun
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser
from src.services.studytomy.adaptive_access import _is_learning_eligible
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.consents import has_current_consent_scope
from src.services.studytomy.entitlements import (
    current_allocation_delivery_policy,
    resolve_active_course_grants,
)
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization
from src.services.studytomy.prerequisites import (
    approved_pass_delivery_activities,
    course_completion_text_activities,
    managed_assessed_pass_status,
    ungated_by_assessed_pass,
)
from src.services.studytomy.releases import (
    _MAX_RELEASE_CAPTION_BYTES,
    _NATIVE_ACTIVITY_MEDIA,
    _RELEASE_MEDIA_TYPES,
    _ready_caption_codes,
)
from src.services.utils.upload_content import open_content_stream

_MAX_RELEASE_MEDIA_BYTES = 500 * 1024 * 1024
_RELEASE_MEDIA_STAGE_TIMEOUT_SECONDS = 300
_RELEASE_MEDIA_FILENAME = {
    "application/pdf": "lesson.pdf",
    "video/mp4": "lesson.mp4",
    "video/webm": "lesson.webm",
    "text/vtt": "captions.vtt",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ManagedReleaseOverview(BaseModel):
    course_uuid: str
    release_uuid: str
    name: str


class ManagedCompletionReceipt(BaseModel):
    activity_uuid: str
    activity_completed: bool
    course_completed: bool


class ManagedTextLesson(ManagedReleaseOverview):
    activity_uuid: str
    paragraphs: list[str]
    completion: ManagedCompletionReceipt | None = Field(default=None, exclude_if=lambda value: value is None)


class ManagedLinkedAssignmentSummary(BaseModel):
    activity_uuid: str
    assignment_uuid: str
    title: str


class ManagedVirtualLabLesson(ManagedReleaseOverview):
    activity_uuid: str
    lab_id: str
    experiment_id: str
    build_version: str
    build_sha256: str
    entry_url: str
    units_system: str
    submission_formats_note: str | None = None
    linked_assignment: ManagedLinkedAssignmentSummary | None = None


class ManagedAssignmentTaskSummary(BaseModel):
    assignment_task_uuid: str
    title: str
    description: str
    assignment_type: str
    max_grade_value: int | None = None
    student_grade: int | None = None
    teacher_feedback: str | None = None
    contents: dict[str, Any] = Field(default_factory=dict)
    submitted_answer: dict[str, Any] | None = None


class ManagedLinkedAssignment(ManagedReleaseOverview):
    activity_uuid: str
    assignment_uuid: str
    submission_uuid: str | None = None
    title: str
    description: str
    due_date: str | None = None
    grading_type: str
    submission_status: str | None = None
    grade: int | None = None
    max_grade: int = 0
    overall_feedback: str | None = None
    attempt_number: int | None = None
    past_due: bool = False
    can_submit: bool = False
    can_retry: bool = False
    allow_retries: bool = False
    max_retries: int = 0
    release_conflict_uuid: str | None = None
    tasks: list[ManagedAssignmentTaskSummary] = Field(default_factory=list)
    accepted_file_formats: list[str] = Field(
        default_factory=lambda: ["PDF", "ZIP", "PNG", "JPG", "DOCX", "XLSX", "PPTX"]
    )
    manual_review_note: str = (
        "Practical lab reports are reviewed manually by your teacher and do not automatically award independent mastery."
    )


class ManagedTaskFileUploadReceipt(BaseModel):
    file_uuid: str
    assignment_task_uuid: str


class ManagedSubmissionDownload(BaseModel):
    filename: str
    media_type: str
    directory: str
    org_uuid: str


@dataclass(slots=True)
class ManagedActivityMedia:
    file: BinaryIO
    media_type: str
    filename: str
    size_bytes: int
    start: int
    end: int
    status_code: int

    async def stream(self) -> AsyncIterator[bytes]:
        remaining = self.end - self.start + 1
        try:
            await asyncio.to_thread(self.file.seek, self.start)
            while remaining > 0:
                chunk = await asyncio.to_thread(
                    self.file.read, min(64 * 1024, remaining)
                )
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk
        finally:
            await asyncio.to_thread(self.file.close)

    async def close(self) -> None:
        if not self.file.closed:
            await asyncio.to_thread(self.file.close)


@dataclass(frozen=True, slots=True)
class ManagedReleaseAuthorization:
    """Current actor and grant authority for one exact immutable release."""

    profile: StudyLearnerProfile
    release: StudyCourseRelease
    progression_policy: StudyPolicyVersion
    grants: tuple[StudyCourseAccessGrant, ...]
    course_id: int
    current_policy_version_ids: tuple[int, ...]


class ManagedReleaseCatalog(BaseModel):
    releases: list[ManagedReleaseOverview]
    next_cursor: str | None


class ManagedLessonSummary(BaseModel):
    activity_uuid: str
    name: str
    position: int
    format: Literal["text", "pdf", "video", "virtual_lab", "unsupported"]
    media_type: str | None
    available: bool
    caption_codes: list[str] = Field(default_factory=list, exclude_if=lambda codes: not codes)
    linked_assignment_activity_uuid: str | None = Field(default=None, exclude_if=lambda value: value is None)
    unavailable_reason: Literal[
        "release_content_unavailable",
        "release_media_unavailable",
        "release_lab_unavailable",
        "unsupported_release_format",
        "release_format_conflict",
        "assessment_pass_required",
        "assessment_pass_delivery_pending",
        "accessibility_policy_required",
        "accessibility_policy_incomplete",
        "media_format_not_approved",
        "captions_or_text_alternative_not_retained",
    ] | None


class ManagedLessonIndex(BaseModel):
    requires_first_use: bool
    lessons: list[ManagedLessonSummary]


class ManagedAccessHistory(ManagedReleaseOverview):
    status: Literal["active", "first_use_pending", "scheduled", "expired", "revoked"]
    start_basis: str
    starts_at: datetime | None
    ends_at: datetime | None
    duration_days: int | None
    completion_before_window: Literal[True] | None = None


def _unavailable() -> HTTPException:
    return HTTPException(
        404, "Course release unavailable.", headers={"Cache-Control": "private, no-store"},
    )


async def _authorized_learner(
    db: AsyncSession, org: Organization, actor: PublicUser, now: datetime,
) -> tuple[StudyLearnerProfile, StudyPolicyVersion, tuple[int, ...]]:
    profile = (await db.execute(select(StudyLearnerProfile).where(
        StudyLearnerProfile.org_id == org.id, StudyLearnerProfile.user_id == actor.id,
        StudyLearnerProfile.status == "active",
        StudyLearnerProfile.control_mode.in_(("independent", "dependent")),
        select(UserOrganization.id).where(
            UserOrganization.org_id == org.id, UserOrganization.user_id == actor.id,
        ).exists(),
    ))).scalar_one_or_none()
    if profile is None:
        raise _unavailable()
    try:
        policies = await _current_policies(db, org, (
            StudyPolicyKey.D_03, StudyPolicyKey.D_05, StudyPolicyKey.D_09, StudyPolicyKey.D_11,
        ), now)
    except HTTPException as error:
        if error.status_code != 409:
            raise
        raise _unavailable() from error
    if not await _is_learning_eligible(db, org, profile, now) or not await has_current_consent_scope(
        db, org, profile, StudyConsentPurpose.LEARNING_TERMS, "course_release", now,
    ):
        raise _unavailable()
    progression = next(policy for policy in policies if policy.policy_key == StudyPolicyKey.D_09.value)
    return profile, progression, tuple(int(policy.id) for policy in policies if policy.id is not None)


def _reviewed_releases(org_id: int):
    return (
        select(StudyCourseRelease)
        .join(Course, (Course.org_id == StudyCourseRelease.org_id)
              & (Course.course_uuid == StudyCourseRelease.course_uuid))
        .join(StudyCourseReview, StudyCourseReview.id == StudyCourseRelease.review_id)
        .where(
            StudyCourseRelease.org_id == org_id,
            Course.studytomy_managed.is_(True),
            Course.public.is_(False),
            Course.published.is_(False),
            StudyCourseRelease.policy_binding.is_not(None),
            StudyCourseReview.org_id == org_id,
            StudyCourseReview.course_uuid == StudyCourseRelease.course_uuid,
            StudyCourseReview.decision == "approved",
            StudyCourseReview.revision_sha256 == StudyCourseRelease.snapshot_sha256,
        )
    )


async def resolve_managed_release_authority(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    release_uuid: str,
    *,
    now: datetime,
    include_unstarted: bool = False,
) -> ManagedReleaseAuthorization:
    """Resolve current learner authority and every grant for one frozen release.

    ``include_unstarted`` is only for a caller that will finish validating the
    requested released source before claiming a first-use grant.
    """
    if now.utcoffset() is None:
        raise ValueError("Release authorization requires a timezone-aware time")
    now = now.astimezone(UTC)
    profile, progression, current_policy_ids = await _authorized_learner(
        db, org, actor, now
    )
    release = (
        await db.execute(
            _reviewed_releases(org.id).where(
                StudyCourseRelease.course_uuid == course_uuid,
                StudyCourseRelease.uuid == release_uuid,
            )
        )
    ).scalar_one_or_none()
    if release is None or canonical_request_hash(release.snapshot) != release.snapshot_sha256:
        raise _unavailable()

    course_ids = (
        await db.execute(
            select(Course.id).where(
                Course.org_id == org.id,
                Course.course_uuid == course_uuid,
                Course.studytomy_managed.is_(True),
                Course.public.is_(False),
                Course.published.is_(False),
            )
        )
    ).scalars().all()
    snapshot_course = release.snapshot.get("course")
    if (
        len(course_ids) != 1
        or not isinstance(snapshot_course, dict)
        or type(snapshot_course.get("id")) is not int
        or snapshot_course.get("id") != course_ids[0]
        or snapshot_course.get("org_id") != org.id
        or snapshot_course.get("course_uuid") != course_uuid
        or release.progression_policy_id != progression.id
    ):
        raise _unavailable()

    grants = tuple(
        await resolve_active_course_grants(
            db,
            org_id=org.id,
            learner_profile_id=profile.id,
            course_uuid=course_uuid,
            release_id=release.id,
            now=now,
            include_unstarted=include_unstarted,
        )
    )
    if not grants:
        raise _unavailable()
    return ManagedReleaseAuthorization(
        profile=profile,
        release=release,
        progression_policy=progression,
        grants=grants,
        course_id=course_ids[0],
        current_policy_version_ids=current_policy_ids,
    )


async def resolve_single_started_assessment_release(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_id: int,
    now: datetime,
) -> StudyCourseRelease | None:
    """Resolve an unambiguous active release without consuming a first-use grant."""
    candidates = (
        await db.execute(
            select(StudyCourseRelease.course_uuid, StudyCourseRelease.uuid)
            .join(StudyCourseAccessGrant, StudyCourseAccessGrant.release_id == StudyCourseRelease.id)
            .join(StudyLearnerProfile, StudyLearnerProfile.id == StudyCourseAccessGrant.learner_profile_id)
            .join(Course, Course.course_uuid == StudyCourseRelease.course_uuid)
            .where(
                Course.id == course_id, Course.org_id == org.id,
                StudyCourseRelease.org_id == org.id,
                StudyCourseAccessGrant.org_id == org.id,
                StudyLearnerProfile.org_id == org.id,
                StudyLearnerProfile.user_id == actor.id,
                StudyCourseAccessGrant.revoked_at.is_(None),
                StudyCourseAccessGrant.created_at <= now,
                StudyCourseAccessGrant.starts_at <= now,
                StudyCourseAccessGrant.ends_at > now,
            )
            .distinct()
            .limit(2)
        )
    ).all()
    if len(candidates) != 1:
        return None
    course_uuid, release_uuid = candidates[0]
    try:
        authority = await resolve_managed_release_authority(
            db, org, actor, course_uuid, release_uuid, now=now,
        )
    except HTTPException as error:
        if error.status_code == 404:
            return None
        raise
    return authority.release


async def _authorized_release(
    db: AsyncSession, org: Organization, actor: PublicUser, course_uuid: str, release_uuid: str,
    *, require_active_grant: bool = True,
) -> tuple[StudyCourseRelease, StudyPolicyVersion, int]:
    now = datetime.now(UTC)
    profile, progression, _current_policy_ids = await _authorized_learner(
        db, org, actor, now
    )
    release = (await db.execute(_reviewed_releases(org.id).where(
        StudyCourseRelease.course_uuid == course_uuid, StudyCourseRelease.uuid == release_uuid,
    ))).scalar_one_or_none()
    if release is None:
        raise _unavailable()
    if require_active_grant and not await resolve_active_course_grants(
        db, org_id=org.id, learner_profile_id=profile.id, course_uuid=course_uuid,
        release_id=release.id, now=now,
    ):
        raise _unavailable()
    if canonical_request_hash(release.snapshot) != release.snapshot_sha256:
        raise _unavailable()
    return release, progression, profile.id


async def list_my_managed_releases(
    db: AsyncSession, org: Organization, actor: PublicUser, *, after: str | None, limit: int,
) -> ManagedReleaseCatalog:
    now = datetime.now(UTC)
    profile, _, _ = await _authorized_learner(db, org, actor, now)
    grant = StudyCourseAccessGrant
    available = select(grant.id).where(
        grant.org_id == org.id, grant.learner_profile_id == profile.id,
        grant.course_uuid == StudyCourseRelease.course_uuid, grant.release_id == StudyCourseRelease.id,
        grant.revoked_at.is_(None), grant.created_at <= now,
        or_(
            and_(grant.starts_at <= now, grant.ends_at > now),
            and_(grant.start_basis == "after_first_access", grant.starts_at.is_(None), grant.ends_at.is_(None)),
        ),
    ).exists()
    query = _reviewed_releases(org.id).where(available)
    return await _page_release_catalog(db, query, after, limit)


async def _page_release_catalog(
    db: AsyncSession, query: Select, after: str | None, limit: int,
) -> ManagedReleaseCatalog:
    if after is not None:
        query = query.where(StudyCourseRelease.uuid > after)
    rows = (await db.execute(query.order_by(StudyCourseRelease.uuid).limit(limit + 1))).scalars().all()
    entries = []
    for row in rows[:limit]:
        course = row.snapshot.get("course")
        name = course.get("name") if isinstance(course, dict) else None
        if isinstance(name, str) and name.strip() and canonical_request_hash(row.snapshot) == row.snapshot_sha256:
            entries.append(ManagedReleaseOverview(course_uuid=row.course_uuid, release_uuid=row.uuid, name=name))
    return ManagedReleaseCatalog(releases=entries, next_cursor=rows[limit - 1].uuid if len(rows) > limit else None)


async def read_managed_release_overview(
    db: AsyncSession, org: Organization, actor: PublicUser, course_uuid: str, release_uuid: str,
) -> ManagedReleaseOverview:
    release, _, _ = await _authorized_release(db, org, actor, course_uuid, release_uuid)
    course = release.snapshot.get("course")
    name = course.get("name") if isinstance(course, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise _unavailable()
    return ManagedReleaseOverview(course_uuid=course_uuid, release_uuid=release_uuid, name=name)


async def _history_retention_policy(
    db: AsyncSession, org: Organization, now: datetime,
) -> tuple[StudyPolicyVersion, list[str]]:
    try:
        information, retention = await _current_policies(
            db, org, (StudyPolicyKey.D_12, StudyPolicyKey.D_13), now,
        )
    except HTTPException as error:
        if error.status_code != 409:
            raise
        raise _unavailable() from error
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_12, information.body)
    uses = body.specified_value("information_uses") if body else None
    scopes = uses.get("learning_access_history") if isinstance(uses, dict) else None
    if not isinstance(scopes, list) or "access_window" not in scopes:
        raise _unavailable()
    return retention, scopes


async def list_my_managed_access_history(
    db: AsyncSession, org: Organization, actor: PublicUser, *, after: str | None, limit: int,
) -> ManagedReleaseCatalog:
    now = datetime.now(UTC)
    profile, _, _ = await _authorized_learner(db, org, actor, now)
    retention, _ = await _history_retention_policy(db, org, now)
    grant = StudyCourseAccessGrant
    history = select(grant.id).where(
        grant.org_id == org.id, grant.learner_profile_id == profile.id,
        grant.course_uuid == StudyCourseRelease.course_uuid,
        grant.release_id == StudyCourseRelease.id, grant.created_at <= now,
    ).exists()
    query = _reviewed_releases(org.id).where(StudyCourseRelease.retention_policy_id == retention.id, history)
    return await _page_release_catalog(db, query, after, limit)


async def read_managed_access_history(
    db: AsyncSession, org: Organization, actor: PublicUser, course_uuid: str, release_uuid: str,
) -> ManagedAccessHistory:
    release, _, profile_id = await _authorized_release(
        db, org, actor, course_uuid, release_uuid, require_active_grant=False,
    )
    now = datetime.now(UTC)
    retention, scopes = await _history_retention_policy(db, org, now)
    if retention.id != release.retention_policy_id:
        raise _unavailable()
    grant = StudyCourseAccessGrant
    priority = case(
        (grant.revoked_at.is_not(None), 4),
        (and_(grant.starts_at <= now, grant.ends_at > now), 0),
        (grant.starts_at.is_(None), 1),
        (grant.starts_at > now, 2),
        else_=3,
    )
    row = (await db.execute(select(grant, priority).where(
        grant.org_id == org.id, grant.learner_profile_id == profile_id,
        grant.course_uuid == course_uuid, grant.release_id == release.id, grant.created_at <= now,
    ).order_by(priority, grant.ends_at.desc().nulls_last(), grant.id.desc()).limit(1))).one_or_none()
    course = release.snapshot.get("course")
    name = course.get("name") if isinstance(course, dict) else None
    if row is None or not isinstance(name, str) or not name.strip():
        raise _unavailable()
    window, rank = row
    completion_before_window = None
    if "course_completion" in scopes:
        completion_before_window = await _retained_completion_before_window(
            db, org.id, actor.id, course_uuid, retention, window.starts_at, now,
        )
    return ManagedAccessHistory(
        course_uuid=course_uuid, release_uuid=release_uuid, name=name,
        status=("active", "first_use_pending", "scheduled", "expired", "revoked")[rank],
        start_basis=window.start_basis, starts_at=window.starts_at, ends_at=window.ends_at,
        duration_days=window.duration_days,
        completion_before_window=completion_before_window,
    )


async def _retained_completion_before_window(
    db: AsyncSession, org_id: int, learner_user_id: int, course_uuid: str,
    retention: StudyPolicyVersion, starts_at: datetime | None, now: datetime,
) -> Literal[True] | None:
    from src.services.studytomy.record_delivery import has_reviewed_completion_before_window

    body, _ = inspect_study_policy_body(StudyPolicyKey.D_13, retention.body)
    record_access = body.specified_value("record_access") if body else None
    rules = body.specified_value("retention") if body else None
    days = rules.get("course_completion_days") if isinstance(rules, dict) else None
    if (
        starts_at is None or not isinstance(record_access, dict)
        or record_access.get("self_course_completion") is not True
        or type(days) is not int or not 1 <= days <= 36500
    ):
        return None
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=UTC)
    earliest = now - timedelta(days=days)
    if await has_reviewed_completion_before_window(
        db, org_id, learner_user_id, course_uuid, earliest, starts_at, now,
    ):
        return True
    updated = (await db.execute(select(TrailRun.update_date).join(Course, Course.id == TrailRun.course_id).where(
        Course.org_id == org_id, Course.course_uuid == course_uuid,
        TrailRun.org_id == org_id, TrailRun.user_id == learner_user_id,
        TrailRun.status == StatusEnum.STATUS_COMPLETED,
    ).limit(2))).scalars().all()
    if len(updated) != 1:
        return None
    try:
        recorded_at = datetime.fromisoformat(updated[0])
    except ValueError:
        return None
    if recorded_at.tzinfo is not None and earliest <= recorded_at < starts_at and recorded_at <= now:
        return True
    return None


def _plain_paragraphs(content: object) -> list[str]:
    if not isinstance(content, dict) or content.get("type") != "doc":
        raise _unavailable()
    blocks = content.get("content")
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= 300:
        raise _unavailable()
    paragraphs = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("type") not in ("paragraph", "heading"):
            raise _unavailable()
        children = block.get("content", [])
        if not isinstance(children, list) or len(children) > 300:
            raise _unavailable()
        parts = []
        for child in children:
            if not isinstance(child, dict) or child.get("type") != "text" or not isinstance(child.get("text"), str):
                raise _unavailable()
            parts.append(child["text"])
        paragraphs.append("".join(parts))
    if not any(text.strip() for text in paragraphs) or sum(map(len, paragraphs)) > 100_000:
        raise _unavailable()
    return paragraphs


async def _assessed_pass_delivery(
    db: AsyncSession, release: StudyCourseRelease, policy: StudyPolicyVersion,
    user_id: int, activity_uuid: str, *, lock_result: bool = False,
) -> tuple[bool, Literal["assessment_pass_required", "assessment_pass_delivery_pending"] | None]:
    body, blockers = inspect_study_policy_body(StudyPolicyKey.D_09, policy.body)
    if body is None or any(blockers.values()) or policy.id != release.progression_policy_id:
        return False, None
    try:
        approved = approved_pass_delivery_activities(body, release.course_uuid)
    except ValidationError:
        return False, "assessment_pass_required"
    status = await managed_assessed_pass_status(
        db, body, release, user_id, activity_uuid, lock_result=lock_result,
    )
    if activity_uuid in approved and status is None:
        return False, "assessment_pass_required"
    return status == "assessment_pass_delivery_pending" and activity_uuid in approved, status


def _project_text_lesson(
    release: StudyCourseRelease, policy: StudyPolicyVersion, activity_uuid: str,
    *, assessed_pass_verified: bool = False,
) -> ManagedTextLesson:
    body, blockers = inspect_study_policy_body(StudyPolicyKey.D_09, policy.body)
    schedule = body.specified_value("release_schedule") if body else None
    allowed = schedule.get("ungated_text_activities") if isinstance(schedule, dict) else None
    activities = allowed.get(release.course_uuid) if isinstance(allowed, dict) else None
    if (
        policy.id != release.progression_policy_id
        or body is None
        or any(blockers.values())
        or (not assessed_pass_verified and not ungated_by_assessed_pass(body, release.course_uuid, activity_uuid))
        or not isinstance(activities, list)
        or activity_uuid not in activities
    ):
        raise _unavailable()
    snapshot = release.snapshot
    course = snapshot.get("course")
    if (
        not isinstance(course, dict)
        or course.get("drip_settings") is not None
        or snapshot.get("schema") != "course_content_evidence_v4"
    ):
        raise _unavailable()
    matches = [row for row in snapshot.get("activities", [])
               if isinstance(row, dict) and row.get("activity_uuid") == activity_uuid]
    if len(matches) != 1:
        raise _unavailable()
    lesson = matches[0]
    if (
        lesson.get("published") is not True or lesson.get("activity_type") != "TYPE_DYNAMIC"
        or lesson.get("activity_sub_type") != "SUBTYPE_DYNAMIC_PAGE"
        or lesson.get("lock_type") not in ("public", "authenticated")
        or not isinstance(lesson.get("name"), str) or not lesson["name"].strip()
    ):
        raise _unavailable()
    parents = [row[0] for row in snapshot.get("activity_order", [])
               if isinstance(row, list) and len(row) == 3 and row[1] == lesson.get("id")]
    if len(parents) != 1:
        raise _unavailable()
    chapters = [row for row in snapshot.get("chapters", [])
                if isinstance(row, dict) and row.get("id") == parents[0]]
    if (
        len(chapters) != 1 or chapters[0].get("lock_type") not in ("public", "authenticated")
        or chapters[0].get("drip_rule") is not None
    ):
        raise _unavailable()
    return ManagedTextLesson(
        course_uuid=release.course_uuid, release_uuid=release.uuid, activity_uuid=activity_uuid,
        name=lesson["name"], paragraphs=_plain_paragraphs(lesson.get("content")),
    )


def _project_media_activity(
    release: StudyCourseRelease,
    policy: StudyPolicyVersion,
    activity_uuid: str,
    *,
    caption_code: str | None = None,
    assessed_pass_verified: bool = False,
) -> tuple[str, dict, str]:
    snapshot = release.snapshot
    course = snapshot.get("course")
    if (
        not isinstance(course, dict)
        or course.get("drip_settings") is not None
        or snapshot.get("schema") != "course_content_evidence_v4"
    ):
        raise _unavailable()
    matches = [
        row
        for row in snapshot.get("activities", [])
        if isinstance(row, dict) and row.get("activity_uuid") == activity_uuid
    ]
    if len(matches) != 1:
        raise _unavailable()
    activity = matches[0]
    subtype = activity.get("activity_sub_type")
    directory = _NATIVE_ACTIVITY_MEDIA.get(subtype)
    if (
        directory is None
        or activity.get("published") is not True
        or activity.get("lock_type") not in ("public", "authenticated")
        or (
            subtype == "SUBTYPE_DOCUMENT_PDF"
            and activity.get("activity_type") != "TYPE_DOCUMENT"
        )
        or (
            subtype == "SUBTYPE_VIDEO_HOSTED"
            and activity.get("activity_type") != "TYPE_VIDEO"
        )
    ):
        raise _unavailable()
    parent_ids = [
        row[0]
        for row in snapshot.get("activity_order", [])
        if isinstance(row, list)
        and len(row) == 3
        and row[1] == activity.get("id")
    ]
    if len(parent_ids) != 1:
        raise _unavailable()
    chapters = [
        row
        for row in snapshot.get("chapters", [])
        if isinstance(row, dict) and row.get("id") == parent_ids[0]
    ]
    if (
        len(chapters) != 1
        or chapters[0].get("lock_type") not in ("public", "authenticated")
        or chapters[0].get("drip_rule") is not None
    ):
        raise _unavailable()
    body, blockers = inspect_study_policy_body(
        StudyPolicyKey.D_09, policy.body
    )
    schedule = body.specified_value("release_schedule") if body else None
    allowed = (
        schedule.get("ungated_media_activities")
        if isinstance(schedule, dict)
        else None
    )
    course_activities = (
        allowed.get(release.course_uuid) if isinstance(allowed, dict) else None
    )
    if (
        policy.id != release.progression_policy_id
        or body is None
        or any(blockers.values())
        or (not assessed_pass_verified and not ungated_by_assessed_pass(body, release.course_uuid, activity_uuid))
        or not isinstance(course_activities, list)
        or activity_uuid not in course_activities
    ):
        raise _unavailable()
    content = activity.get("content")
    filename = content.get("filename") if isinstance(content, dict) else None
    normalized = (
        filename.replace("\\", "/") if isinstance(filename, str) else ""
    )
    if (
        not filename
        or len(filename) > 255
        or PurePosixPath(normalized).name != normalized
        or normalized in {".", ".."}
    ):
        raise _unavailable()
    media_type = _RELEASE_MEDIA_TYPES.get(PurePosixPath(normalized).suffix.lower())
    if (
        media_type is None
        or (subtype == "SUBTYPE_DOCUMENT_PDF" and media_type != "application/pdf")
        or (subtype == "SUBTYPE_VIDEO_HOSTED" and not media_type.startswith("video/"))
    ):
        raise _unavailable()
    source_reference = (
        f"courses/{release.course_uuid}/activities/{activity_uuid}/{directory}/{normalized}"
    )
    if caption_code is not None:
        if subtype != "SUBTYPE_VIDEO_HOSTED" or caption_code not in _ready_caption_codes(activity):
            raise _unavailable()
        source_reference = (
            f"courses/{release.course_uuid}/activities/{activity_uuid}/video/captions/{caption_code}.vtt"
        )
        media_type = "text/vtt"
    media = release.media_manifest.get(source_reference)
    if (
        not isinstance(media, dict)
        or media.get("media_type") != media_type
        or not _SHA256.fullmatch(str(media.get("sha256", "")))
        or type(media.get("size_bytes")) is not int
        or media["size_bytes"] < 1
        or (caption_code is not None and media["size_bytes"] > _MAX_RELEASE_CAPTION_BYTES)
    ):
        raise _unavailable()
    storage_reference = media.get("storage_reference")
    storage_path = PurePosixPath(
        storage_reference.replace("\\", "/")
        if isinstance(storage_reference, str)
        else "."
    )
    if (
        not isinstance(storage_reference, str)
        or storage_path.parts[:2] != ("studytomy", "releases")
        or len(storage_path.parts) != 4
        or ".." in storage_path.parts
        or storage_path.suffix != ".bin"
        or str(storage_path) != storage_reference
    ):
        raise _unavailable()
    return source_reference, media, media_type


def _parse_media_range(range_header: str | None, size_bytes: int) -> tuple[int, int, int]:
    def invalid() -> HTTPException:
        return HTTPException(
            416,
            "Requested media range is not satisfiable.",
            headers={
                "Content-Range": f"bytes */{size_bytes}",
                "Accept-Ranges": "bytes",
                "Cache-Control": "private, no-store",
            },
        )

    if size_bytes < 1:
        raise invalid()
    if not range_header:
        return 0, size_bytes - 1, 200
    if not range_header.startswith("bytes=") or "," in range_header:
        raise invalid()
    start_text, separator, end_text = range_header[6:].partition("-")
    if not separator or (not start_text and not end_text):
        raise invalid()
    if not start_text:
        if not end_text.isdecimal() or int(end_text) < 1:
            raise invalid()
        length = min(int(end_text), size_bytes)
        return size_bytes - length, size_bytes - 1, 206
    if not start_text.isdecimal() or (end_text and not end_text.isdecimal()):
        raise invalid()
    start = int(start_text)
    end = min(int(end_text), size_bytes - 1) if end_text else size_bytes - 1
    if start >= size_bytes or end < start:
        raise invalid()
    return start, end, 206


def _validate_media_delivery_policy(
    policy: StudyPolicyVersion,
    media_type: str,
    size_bytes: int,
    *,
    has_captions: bool = False,
) -> None:
    available, reason = _managed_media_availability(
        policy, media_type, size_bytes, has_captions=has_captions,
    )
    if available:
        return
    if reason == "accessibility_policy_incomplete":
        raise studytomy_http_error(
            409,
            "POLICY_NOT_APPROVED",
            "The approved accessibility policy must define supported media formats and a size limit.",
        )
    raise studytomy_http_error(
        409,
        "RELEASE_MEDIA_UNSUPPORTED",
        "The release does not retain captions or an approved accessible alternative."
        if reason == "captions_or_text_alternative_not_retained"
        else "The approved accessibility policy does not authorize this resource format or size.",
    )


def _approved_caption_codes(
    release: StudyCourseRelease,
    progression: StudyPolicyVersion,
    policy: StudyPolicyVersion | None,
    activity_uuid: str,
    *,
    assessed_pass_verified: bool = False,
) -> list[str]:
    activity = next(
        row for row in release.snapshot["activities"]
        if isinstance(row, dict) and row.get("activity_uuid") == activity_uuid
    )
    approved = []
    for code in _ready_caption_codes(activity):
        try:
            _, track, track_type = _project_media_activity(
                release, progression, activity_uuid, caption_code=code,
                assessed_pass_verified=assessed_pass_verified,
            )
        except HTTPException as error:
            if error.status_code != 404:
                raise
            continue
        available, _ = _managed_media_availability(policy, track_type, track["size_bytes"])
        if available:
            approved.append(code)
    return approved


def _project_authorized_media(
    release: StudyCourseRelease,
    progression: StudyPolicyVersion,
    policy: StudyPolicyVersion,
    activity_uuid: str,
    caption_code: str | None,
    *,
    assessed_pass_verified: bool = False,
) -> tuple[str, dict, str]:
    source, media, media_type = _project_media_activity(
        release, progression, activity_uuid, assessed_pass_verified=assessed_pass_verified,
    )
    codes = (
        _approved_caption_codes(
            release, progression, policy, activity_uuid, assessed_pass_verified=assessed_pass_verified,
        )
        if media_type.startswith("video/") else []
    )
    _validate_media_delivery_policy(policy, media_type, media["size_bytes"], has_captions=bool(codes))
    if caption_code is None:
        return source, media, media_type
    if caption_code not in codes:
        raise _unavailable()
    return _project_media_activity(
        release, progression, activity_uuid, caption_code=caption_code,
        assessed_pass_verified=assessed_pass_verified,
    )


async def _stage_release_media(org_uuid: str, media: dict) -> BinaryIO:
    # The response stream owns this spool and closes it after the final byte.
    buffer = TemporaryFile(mode="w+b")  # noqa: SIM115
    reference = media["storage_reference"]
    directory, filename = reference.rsplit("/", 1)
    digest = hashlib.sha256()
    size = 0
    staged = False
    try:
        async with asyncio.timeout(_RELEASE_MEDIA_STAGE_TIMEOUT_SECONDS):
            stream = await open_content_stream(
                directory=directory,
                type_of_dir="orgs",
                uuid=org_uuid,
                file_and_format=filename,
            )
            async with aclosing(stream) as chunks:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > media["size_bytes"]:
                        raise ValueError("Retained media exceeds its approved size.")
                    digest.update(chunk)
                    await asyncio.to_thread(buffer.write, chunk)
            if size != media["size_bytes"] or digest.hexdigest() != media["sha256"]:
                raise ValueError("Retained media does not match its approved digest.")
            await asyncio.to_thread(buffer.seek, 0)
        staged = True
        return buffer
    except TimeoutError as error:
        raise studytomy_http_error(
            503,
            "RELEASE_STORAGE_TIMEOUT",
            "The retained course resource could not be prepared for delivery in time.",
        ) from error
    except Exception as error:
        raise studytomy_http_error(
            503,
            "RELEASE_STORAGE_INVALID",
            "The retained course resource could not be verified for delivery.",
        ) from error
    finally:
        if not staged and not buffer.closed:
            buffer.close()


async def read_managed_activity_media(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    release_uuid: str,
    activity_uuid: str,
    *,
    range_header: str | None = None,
    start_first_use: bool = False,
    caption_code: str | None = None,
) -> ManagedActivityMedia:
    org_id = org.id
    org_uuid = org.org_uuid
    org = await lock_organization(db, org_id)
    release, progression, profile_id = await _authorized_release(
        db,
        org,
        actor,
        course_uuid,
        release_uuid,
        require_active_grant=False,
    )
    candidates = await resolve_active_course_grants(
        db,
        org_id=org.id,
        learner_profile_id=profile_id,
        course_uuid=course_uuid,
        release_id=release.id,
        now=datetime.now(UTC),
        include_unstarted=caption_code is None,
    )
    if not candidates:
        raise _unavailable()
    policy = (await _current_policies(
        db, org, (StudyPolicyKey.D_15,), datetime.now(UTC)
    ))[0]
    assessed_pass_verified, _ = await _assessed_pass_delivery(
        db, release, progression, actor.id, activity_uuid, lock_result=True,
    )
    source_reference, media, media_type = _project_authorized_media(
        release, progression, policy, activity_uuid, caption_code,
        assessed_pass_verified=assessed_pass_verified,
    )
    start, end, status_code = _parse_media_range(range_header, media["size_bytes"])
    release_id = release.id
    release_digest = release.snapshot_sha256
    progression_id = progression.id
    policy_id = policy.id
    media_snapshot = dict(media)

    # Remote or slow storage must not hold the organization governance lock.
    # All authority is checked again below before returning any media bytes.
    await db.commit()
    staged = await _stage_release_media(org_uuid, media_snapshot)
    try:
        org = await lock_organization(db, org_id)
        current_release, current_progression, current_profile_id = await _authorized_release(
            db,
            org,
            actor,
            course_uuid,
            release_uuid,
            require_active_grant=False,
        )
        if (
            current_profile_id != profile_id
            or current_release.id != release_id
            or current_release.snapshot_sha256 != release_digest
            or current_progression.id != progression_id
        ):
            raise _unavailable()
        current_policy = (await _current_policies(
            db, org, (StudyPolicyKey.D_15,), datetime.now(UTC)
        ))[0]
        if current_policy.id != policy_id:
            raise studytomy_http_error(
                409,
                "POLICY_CHANGED",
                "The current media-access policy changed while the resource was being prepared. Retry the request.",
            )
        current_pass_verified, _ = await _assessed_pass_delivery(
            db, current_release, current_progression, actor.id, activity_uuid, lock_result=True,
        )
        current_source_reference, current_media, current_media_type = _project_authorized_media(
            current_release, current_progression, current_policy, activity_uuid, caption_code,
            assessed_pass_verified=current_pass_verified,
        )
        if (
            current_source_reference != source_reference
            or current_media_type != media_type
            or current_media != media_snapshot
        ):
            raise _unavailable()

        now = datetime.now(UTC)
        grants = await resolve_active_course_grants(
            db,
            org_id=org_id,
            learner_profile_id=profile_id,
            course_uuid=course_uuid,
            release_id=release_id,
            now=now,
            claim_first_use=start_first_use and caption_code is None,
        )
        if not grants:
            raise _unavailable()
        await _retain_allocation_delivery(db, org, actor, grants, now)
    except BaseException:
        if not staged.closed:
            staged.close()
        raise
    return ManagedActivityMedia(
        file=staged,
        media_type=media_type,
        filename=_RELEASE_MEDIA_FILENAME[media_type],
        size_bytes=media["size_bytes"],
        start=start,
        end=end,
        status_code=status_code,
    )


async def read_managed_text_lesson(
    db: AsyncSession, org: Organization, actor: PublicUser, course_uuid: str,
    release_uuid: str, activity_uuid: str, *, start_first_use: bool = False,
) -> ManagedTextLesson:
    lesson, release, policy, profile_id = await _authorized_text_lesson(
        db, org, actor, course_uuid, release_uuid, activity_uuid, start_first_use=start_first_use,
    )
    if activity_uuid in _required_text_completions(policy, course_uuid):
        prefix = f"studytomy:completion:{org.id}:{profile_id}:{release.uuid}"
        keys = set((await db.execute(select(StudyBusinessEvent.dedupe_key).where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.actor_user_id == actor.id,
            StudyBusinessEvent.dedupe_key.in_((prefix, f"{prefix}:{activity_uuid}")),
        ))).scalars())
        lesson.completion = ManagedCompletionReceipt(
            activity_uuid=activity_uuid, activity_completed=f"{prefix}:{activity_uuid}" in keys,
            course_completed=prefix in keys,
        )
    return lesson


async def _authorized_text_lesson(
    db: AsyncSession, org: Organization, actor: PublicUser, course_uuid: str,
    release_uuid: str, activity_uuid: str, *, start_first_use: bool = False, record_delivery: bool = True,
) -> tuple[ManagedTextLesson, StudyCourseRelease, StudyPolicyVersion, int]:
    org = await lock_organization(db, org.id)
    release, policy, profile_id = await _authorized_release(
        db, org, actor, course_uuid, release_uuid, require_active_grant=False,
    )
    assessed_pass_verified, _ = await _assessed_pass_delivery(
        db, release, policy, actor.id, activity_uuid, lock_result=True,
    )
    result = _project_text_lesson(release, policy, activity_uuid, assessed_pass_verified=assessed_pass_verified)
    now = datetime.now(UTC)
    grants = await resolve_active_course_grants(
        db, org_id=org.id, learner_profile_id=profile_id, course_uuid=course_uuid,
        release_id=release.id, now=now, claim_first_use=start_first_use,
    )
    if not grants:
        raise _unavailable()
    if record_delivery:
        await _retain_allocation_delivery(db, org, actor, grants, now)
    return result, release, policy, profile_id


def _required_text_completions(policy: StudyPolicyVersion, course_uuid: str) -> set[str]:
    body, blockers = inspect_study_policy_body(StudyPolicyKey.D_09, policy.body)
    if body is None or any(blockers.values()):
        return set()
    try:
        return course_completion_text_activities(body, course_uuid)
    except ValueError:
        return set()


async def acknowledge_managed_text_completion(
    db: AsyncSession, org: Organization, actor: PublicUser, course_uuid: str,
    release_uuid: str, activity_uuid: str,
) -> ManagedCompletionReceipt:
    """Record explicit learner acknowledgement against one approved text-course release."""
    _, release, policy, profile_id = await _authorized_text_lesson(
        db, org, actor, course_uuid, release_uuid, activity_uuid, record_delivery=False,
    )
    required = _required_text_completions(policy, course_uuid)
    if activity_uuid not in required:
        raise _unavailable()
    # Authorization locks the organization, so concurrent acknowledgements
    # cannot lose the transition that satisfies the final requirement.
    prefix = f"studytomy:completion:{org.id}:{profile_id}:{release.uuid}"
    records = (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id,
        StudyBusinessEvent.actor_user_id == actor.id,
        StudyBusinessEvent.action == "studytomy.activity.completion_acknowledged",
        StudyBusinessEvent.operation_uuid == release.uuid,
        StudyBusinessEvent.resource_uuid.in_(required),
    ))).scalars()
    completed = {
        event.resource_uuid for event in records
        if event.policy_refs == [policy.id]
        and event.after_state == {"source_snapshot_digest": release.snapshot_sha256}
    }
    if activity_uuid not in completed:
        await append_business_event(
            db, org_id=org.id, actor_user_id=actor.id, actor_capacity="learner",
            action="studytomy.activity.completion_acknowledged", reason_code="LEARNER_ACKNOWLEDGED_COMPLETION",
            resource_kind="release_activity", resource_uuid=activity_uuid,
            operation_uuid=release.uuid, policy_refs=[policy.id],
            after_state={"source_snapshot_digest": release.snapshot_sha256},
            dedupe_key=f"{prefix}:{activity_uuid}",
        )
        completed.add(activity_uuid)
    course_completed = completed == required
    if course_completed and not await db.scalar(select(StudyBusinessEvent.id).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.dedupe_key == prefix,
    )):
        await append_business_event(
            db, org_id=org.id, actor_user_id=actor.id, actor_capacity="learner",
            action="studytomy.course.completion_recorded", reason_code="RELEASE_REQUIREMENTS_COMPLETED",
            resource_kind="course_release", resource_uuid=release.uuid,
            policy_refs=[policy.id], after_state={"source_snapshot_digest": release.snapshot_sha256},
            dedupe_key=prefix,
        )
    return ManagedCompletionReceipt(
        activity_uuid=activity_uuid, activity_completed=True, course_completed=course_completed,
    )


async def _retain_allocation_delivery(
    db: AsyncSession, org: Organization, actor: PublicUser,
    grants: list[StudyCourseAccessGrant], now: datetime,
) -> None:
    """Record the first authorized body delivery for eligible order grants."""
    keys = {
        f"studytomy:course_grant:delivered:{grant.id}": str(grant.id)
        for grant in grants if grant.source_kind == "order_line"
    }
    if not keys:
        return
    policy = await current_allocation_delivery_policy(db, org, now)
    if policy is None:
        return
    existing = set((await db.execute(select(StudyBusinessEvent.dedupe_key).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.dedupe_key.in_(keys),
    ))).scalars().all())
    for key, grant_id in keys.items():
        if key not in existing:
            await append_business_event(
                db, org_id=org.id, actor_user_id=actor.id,
                resource_kind="course_access_grant", resource_uuid=grant_id,
                action="studytomy.course_grant.content_delivered", reason_code="REVIEWED_CONTENT_DELIVERED",
                policy_refs=[policy.id], dedupe_key=key,
            )


def _managed_media_availability(
    policy: StudyPolicyVersion | None,
    media_type: str,
    size_bytes: int,
    *,
    has_captions: bool = False,
) -> tuple[bool, str | None]:
    if policy is None:
        return False, "accessibility_policy_required"

    body, blockers = inspect_study_policy_body(StudyPolicyKey.D_15, policy.body)
    formats = body.specified_value("supported_formats") if body else None
    max_bytes = formats.get("max_bytes") if isinstance(formats, dict) else None
    media_types = formats.get("media_types") if isinstance(formats, dict) else None
    if (
        any(blockers.values())
        or type(max_bytes) is not int
        or not 1 <= max_bytes <= _MAX_RELEASE_MEDIA_BYTES
        or not isinstance(media_types, list)
        or any(not isinstance(value, str) for value in media_types)
    ):
        return False, "accessibility_policy_incomplete"
    if media_type not in media_types or size_bytes > max_bytes:
        return False, "media_format_not_approved"

    if media_type.startswith("video/") and not has_captions:
        return False, "captions_or_text_alternative_not_retained"
    return True, None


async def list_managed_text_lessons(
    db: AsyncSession, org: Organization, actor: PublicUser, course_uuid: str, release_uuid: str,
) -> ManagedLessonIndex:
    release, policy, profile_id = await _authorized_release(
        db, org, actor, course_uuid, release_uuid, require_active_grant=False,
    )
    grants = await resolve_active_course_grants(
        db, org_id=org.id, learner_profile_id=profile_id, course_uuid=course_uuid,
        release_id=release.id, now=datetime.now(UTC), include_unstarted=True,
    )
    if not grants:
        raise _unavailable()
    snapshot = release.snapshot
    chapter_positions = {
        row[0]: row[1] for row in snapshot.get("chapter_order", [])
        if isinstance(row, list) and len(row) == 2 and all(type(value) is int for value in row)
    }
    positions = {
        row[1]: (chapter_positions[row[0]], row[2], row[1])
        for row in snapshot.get("activity_order", [])
        if isinstance(row, list) and len(row) == 3 and all(type(value) is int for value in row)
        and row[0] in chapter_positions
    }
    rows = [
        row for row in snapshot.get("activities", [])
        if isinstance(row, dict) and type(row.get("id")) is int and row["id"] in positions
        and isinstance(row.get("activity_uuid"), str)
    ]
    schedule_body, _ = inspect_study_policy_body(StudyPolicyKey.D_09, policy.body)
    schedule = schedule_body.specified_value("release_schedule") if schedule_body else None
    text_schedule = (
        schedule.get("ungated_text_activities") if isinstance(schedule, dict) else None
    )
    media_schedule = (
        schedule.get("ungated_media_activities") if isinstance(schedule, dict) else None
    )
    lab_schedule = (
        schedule.get("ungated_lab_activities") if isinstance(schedule, dict) else None
    )
    text_ids = text_schedule.get(course_uuid) if isinstance(text_schedule, dict) else None
    media_ids = media_schedule.get(course_uuid) if isinstance(media_schedule, dict) else None
    lab_ids = lab_schedule.get(course_uuid) if isinstance(lab_schedule, dict) else None
    authorized_text = set(text_ids) if isinstance(text_ids, list) and all(
        isinstance(value, str) for value in text_ids
    ) else set()
    authorized_media = set(media_ids) if isinstance(media_ids, list) and all(
        isinstance(value, str) for value in media_ids
    ) else set()
    authorized_labs = set(lab_ids) if isinstance(lab_ids, list) and all(
        isinstance(value, str) for value in lab_ids
    ) else set()

    media_policy: StudyPolicyVersion | None = None
    try:
        media_policy = (
            await _current_policies(
                db, org, (StudyPolicyKey.D_15,), datetime.now(UTC)
            )
        )[0]
    except HTTPException as error:
        if error.status_code != 409:
            raise

    lessons: list[ManagedLessonSummary] = []
    seen_activity_uuids: set[str] = set()
    for row in sorted(rows, key=lambda item: positions[item["id"]]):
        activity_uuid = row["activity_uuid"]
        if activity_uuid in seen_activity_uuids:
            continue
        seen_activity_uuids.add(activity_uuid)
        if (
            row.get("published") is not True
            or row.get("lock_type") not in ("public", "authenticated")
        ):
            continue
        # Assignment titles and answer-key activities are never part of the
        # lesson/media catalog, even if a malformed schedule includes them.
        if (
            row.get("activity_type") == "TYPE_ASSIGNMENT"
            or row.get("activity_sub_type") == "SUBTYPE_ASSIGNMENT_ANY"
        ):
            continue
        is_text = activity_uuid in authorized_text
        is_media = activity_uuid in authorized_media
        is_lab = activity_uuid in authorized_labs
        if not is_text and not is_media and not is_lab:
            continue

        name = row.get("name")
        name = name.strip() if isinstance(name, str) else ""
        if not name:
            name = "Unavailable activity"
        position = len(lessons) + 1

        if sum((is_text, is_media, is_lab)) > 1:
            lessons.append(ManagedLessonSummary(
                activity_uuid=activity_uuid,
                name=name,
                position=position,
                format="unsupported",
                media_type=None,
                available=False,
                unavailable_reason="release_format_conflict",
            ))
            continue

        assessed_pass_verified, pass_status = await _assessed_pass_delivery(
            db, release, policy, actor.id, activity_uuid,
        )
        if is_lab:
            try:
                lab = _project_virtual_lab_activity(
                    release, policy, activity_uuid, assessed_pass_verified=assessed_pass_verified,
                )
            except HTTPException as error:
                if error.status_code != 404:
                    raise
                lessons.append(ManagedLessonSummary(
                    activity_uuid=activity_uuid,
                    name=name,
                    position=position,
                    format="virtual_lab",
                    media_type=None,
                    available=False,
                    unavailable_reason=(pass_status if not assessed_pass_verified else None)
                    or "release_lab_unavailable",
                ))
                continue
            lessons.append(ManagedLessonSummary(
                activity_uuid=lab.activity_uuid,
                name=lab.name,
                position=position,
                format="virtual_lab",
                media_type=None,
                available=True,
                linked_assignment_activity_uuid=(
                    lab.linked_assignment.activity_uuid if lab.linked_assignment else None
                ),
                unavailable_reason=None,
            ))
            continue
        if is_text:
            try:
                lesson = _project_text_lesson(
                    release, policy, activity_uuid, assessed_pass_verified=assessed_pass_verified,
                )
            except HTTPException as error:
                if error.status_code != 404:
                    raise
                lessons.append(ManagedLessonSummary(
                    activity_uuid=activity_uuid,
                    name=name,
                    position=position,
                    format="text",
                    media_type=None,
                    available=False,
                    unavailable_reason=(pass_status if not assessed_pass_verified else None)
                    or "release_content_unavailable",
                ))
                continue
            lessons.append(ManagedLessonSummary(
                activity_uuid=lesson.activity_uuid,
                name=lesson.name,
                position=position,
                format="text",
                media_type=None,
                available=True,
                unavailable_reason=None,
            ))
            continue

        try:
            _source_reference, media, media_type = _project_media_activity(
                release, policy, activity_uuid, assessed_pass_verified=assessed_pass_verified,
            )
        except HTTPException as error:
            if error.status_code != 404:
                raise
            subtype = row.get("activity_sub_type")
            media_format: Literal["pdf", "video", "unsupported"] = (
                "pdf" if subtype == "SUBTYPE_DOCUMENT_PDF"
                else "video" if subtype == "SUBTYPE_VIDEO_HOSTED"
                else "unsupported"
            )
            lessons.append(ManagedLessonSummary(
                activity_uuid=activity_uuid,
                name=name,
                position=position,
                format=media_format,
                media_type=None,
                available=False,
                unavailable_reason=(
                    (pass_status if not assessed_pass_verified else None) or "release_media_unavailable"
                    if media_format != "unsupported"
                    else "unsupported_release_format"
                ),
            ))
            continue
        media_format = "pdf" if media_type == "application/pdf" else "video"
        caption_codes = (
            _approved_caption_codes(
                release, policy, media_policy, activity_uuid, assessed_pass_verified=assessed_pass_verified,
            )
            if media_format == "video" else []
        )
        available, unavailable_reason = _managed_media_availability(
            media_policy, media_type, media["size_bytes"], has_captions=bool(caption_codes),
        )
        lessons.append(ManagedLessonSummary(
            activity_uuid=activity_uuid,
            name=name,
            position=position,
            format=media_format,
            media_type=media_type,
            available=available,
            caption_codes=caption_codes if available else [],
            unavailable_reason=unavailable_reason,
        ))
    return ManagedLessonIndex(
        requires_first_use=all(grant.starts_at is None for grant in grants), lessons=lessons,
    )


def _project_virtual_lab_activity(
    release: StudyCourseRelease,
    policy: StudyPolicyVersion,
    activity_uuid: str,
    *,
    assessed_pass_verified: bool = False,
) -> ManagedVirtualLabLesson:
    """Project an authorized governed virtual lab from an immutable course release snapshot (T-06)."""
    from src.services.studytomy.virtual_labs import validate_governed_virtual_lab_content

    body, blockers = inspect_study_policy_body(StudyPolicyKey.D_09, policy.body)
    schedule = body.specified_value("release_schedule") if body else None
    allowed = schedule.get("ungated_lab_activities") if isinstance(schedule, dict) else None
    activities = allowed.get(release.course_uuid) if isinstance(allowed, dict) else None
    if (
        policy.id != release.progression_policy_id
        or body is None
        or any(blockers.values())
        or (
            not assessed_pass_verified
            and not ungated_by_assessed_pass(body, release.course_uuid, activity_uuid)
        )
        or not isinstance(activities, list)
        or activity_uuid not in activities
    ):
        raise _unavailable()
    snapshot = release.snapshot
    course = snapshot.get("course")
    if (
        not isinstance(course, dict)
        or course.get("drip_settings") is not None
        or snapshot.get("schema") != "course_content_evidence_v4"
    ):
        raise _unavailable()
    matches = [
        row
        for row in snapshot.get("activities", [])
        if isinstance(row, dict) and row.get("activity_uuid") == activity_uuid
    ]
    if len(matches) != 1:
        raise _unavailable()
    activity = matches[0]
    if (
        activity.get("published") is not True
        or activity.get("activity_sub_type") != "SUBTYPE_DYNAMIC_EMBED"
        or activity.get("lock_type") not in ("public", "authenticated")
        or not isinstance(activity.get("name"), str)
        or not activity["name"].strip()
    ):
        raise _unavailable()
    parents = [
        row[0]
        for row in snapshot.get("activity_order", [])
        if isinstance(row, list) and len(row) == 3 and row[1] == activity.get("id")
    ]
    if len(parents) != 1:
        raise _unavailable()
    chapters = [
        row
        for row in snapshot.get("chapters", [])
        if isinstance(row, dict) and row.get("id") == parents[0]
    ]
    if (
        len(chapters) != 1
        or chapters[0].get("lock_type") not in ("public", "authenticated")
        or chapters[0].get("drip_rule") is not None
    ):
        raise _unavailable()

    try:
        descriptor = validate_governed_virtual_lab_content(
            activity.get("content"), snapshot=snapshot
        )
    except HTTPException:
        raise _unavailable() from None

    linked_summary: ManagedLinkedAssignmentSummary | None = None
    if descriptor.linked_assignment_activity_uuid is not None:
        linked_act = next(
            (
                row
                for row in snapshot.get("activities", [])
                if isinstance(row, dict)
                and row.get("activity_uuid") == descriptor.linked_assignment_activity_uuid
                and row.get("activity_sub_type") == "SUBTYPE_ASSIGNMENT_ANY"
                and row.get("published") is True
            ),
            None,
        )
        linked_asg = (
            next(
                (
                    row
                    for row in snapshot.get("assignments", [])
                    if isinstance(row, dict) and row.get("activity_id") == linked_act.get("id")
                ),
                None,
            )
            if linked_act is not None
            else None
        )
        if linked_act is not None and linked_asg is not None:
            linked_summary = ManagedLinkedAssignmentSummary(
                activity_uuid=str(linked_act["activity_uuid"]),
                assignment_uuid=str(linked_asg["assignment_uuid"]),
                title=str(linked_asg.get("title") or linked_act.get("name") or "Practical Assignment"),
            )

    return ManagedVirtualLabLesson(
        course_uuid=release.course_uuid,
        release_uuid=release.uuid,
        activity_uuid=activity_uuid,
        name=activity["name"].strip(),
        lab_id=descriptor.lab_id,
        experiment_id=descriptor.experiment_id,
        build_version=descriptor.build_version,
        build_sha256=descriptor.build_sha256,
        entry_url=descriptor.entry_url,
        units_system=descriptor.units_system,
        submission_formats_note=descriptor.submission_formats_note,
        linked_assignment=linked_summary,
    )


async def read_managed_virtual_lab(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    release_uuid: str,
    activity_uuid: str,
    *,
    start_first_use: bool = False,
) -> ManagedVirtualLabLesson:
    """Authorize and deliver one pinned virtual lab descriptor without granting mastery (T-06)."""
    org = await lock_organization(db, org.id)
    release, policy, profile_id = await _authorized_release(
        db, org, actor, course_uuid, release_uuid, require_active_grant=False,
    )
    assessed_pass_verified, _ = await _assessed_pass_delivery(
        db, release, policy, actor.id, activity_uuid, lock_result=True,
    )
    result = _project_virtual_lab_activity(
        release, policy, activity_uuid, assessed_pass_verified=assessed_pass_verified,
    )
    now = datetime.now(UTC)
    grants = await resolve_active_course_grants(
        db,
        org_id=org.id,
        learner_profile_id=profile_id,
        course_uuid=course_uuid,
        release_id=release.id,
        now=now,
        claim_first_use=start_first_use,
    )
    if not grants:
        raise _unavailable()
    await _retain_allocation_delivery(db, org, actor, grants, now)
    return result


async def _resolve_managed_assignment_target(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    release_uuid: str,
    activity_uuid: str,
) -> tuple[
    StudyCourseRelease,
    StudyPolicyVersion,
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
]:
    release, policy, profile_id = await _authorized_release(
        db, org, actor, course_uuid, release_uuid, require_active_grant=False,
    )
    grants = await resolve_active_course_grants(
        db,
        org_id=org.id,
        learner_profile_id=profile_id,
        course_uuid=course_uuid,
        release_id=release.id,
        now=datetime.now(UTC),
        include_unstarted=False,
    )
    if not grants:
        raise _unavailable()

    snapshot = release.snapshot
    if snapshot.get("schema") != "course_content_evidence_v4":
        raise _unavailable()

    body, blockers = inspect_study_policy_body(StudyPolicyKey.D_09, policy.body)
    if body is None or any(blockers.values()) or policy.id != release.progression_policy_id:
        raise _unavailable()
    schedule = body.specified_value("release_schedule")
    lab_ids = (
        schedule.get("ungated_lab_activities", {}).get(course_uuid)
        if isinstance(schedule, dict) and isinstance(schedule.get("ungated_lab_activities"), dict)
        else None
    )
    asg_ids = (
        schedule.get("ungated_assignment_activities", {}).get(course_uuid)
        if isinstance(schedule, dict) and isinstance(schedule.get("ungated_assignment_activities"), dict)
        else None
    )
    authorized_asg_uuids: set[str] = (
        set(asg_ids)
        if isinstance(asg_ids, list) and all(isinstance(v, str) for v in asg_ids)
        else set()
    )
    if isinstance(lab_ids, list):
        for lab_act_uuid in lab_ids:
            if not isinstance(lab_act_uuid, str):
                continue
            try:
                projected_lab = _project_virtual_lab_activity(release, policy, lab_act_uuid)
            except HTTPException:
                continue
            if projected_lab.linked_assignment is not None:
                authorized_asg_uuids.add(projected_lab.linked_assignment.activity_uuid)

    if activity_uuid not in authorized_asg_uuids:
        raise _unavailable()

    act_row = next(
        (
            row
            for row in snapshot.get("activities", [])
            if isinstance(row, dict)
            and row.get("activity_uuid") == activity_uuid
            and row.get("activity_sub_type") == "SUBTYPE_ASSIGNMENT_ANY"
            and row.get("published") is True
        ),
        None,
    )
    if act_row is None:
        raise _unavailable()
    asg_row = next(
        (
            row
            for row in snapshot.get("assignments", [])
            if isinstance(row, dict) and row.get("activity_id") == act_row.get("id")
        ),
        None,
    )
    if asg_row is None:
        raise _unavailable()

    retained_tasks = [
        row
        for row in snapshot.get("assignment_tasks", [])
        if isinstance(row, dict) and row.get("assignment_id") == asg_row.get("id")
    ]
    retained_tasks.sort(key=lambda r: (r.get("order") is None, r.get("order") or 0, r.get("id") or 0))
    return release, policy, act_row, asg_row, retained_tasks


async def _managed_assignment_deadline_state(
    db: AsyncSession,
    org_id: int,
    assignment_id: int,
    asg_row: dict[str, Any],
    learner_id: int,
) -> tuple[bool, str | None]:
    from src.db.courses.assignments import Assignment
    from src.services.courses.activities.assignments import _assignment_deadline, _is_assignment_past_due
    from src.services.studytomy.assessment_reviews import assessment_deadline_extension

    eff_stub = SimpleNamespace(id=assignment_id, org_id=org_id, due_date=asg_row.get("due_date"))
    base_due = _assignment_deadline(eff_stub)
    live_asg = await db.get(Assignment, assignment_id)
    extension = (
        await assessment_deadline_extension(db, live_asg, learner_id)
        if live_asg is not None
        else None
    )
    if extension is not None:
        past_due = datetime.now(UTC) > extension
        effective_due = extension.isoformat() if base_due is None or extension > (
            base_due.replace(tzinfo=UTC) if base_due.tzinfo is None else base_due.astimezone(UTC)
        ) else str(asg_row.get("due_date"))
        return past_due, effective_due
    return _is_assignment_past_due(eff_stub), (str(asg_row["due_date"]) if asg_row.get("due_date") else None)


_SAFE_MANAGED_TASK_CONTENT_KEYS = frozenset({
    "prompt",
    "question",
    "instructions",
    "description",
    "hint",
    "unit",
    "units",
    "tolerance",
    "placeholder",
    "min",
    "max",
    "step",
})


def _sanitize_managed_task_contents(
    raw_contents: Any, task_type: str, keep_answer_keys: bool
) -> dict[str, Any]:
    from src.services.courses.activities.assignments import _strip_answer_key

    if not isinstance(raw_contents, dict):
        return {}
    stripped = _strip_answer_key(raw_contents, keep_answer_keys=keep_answer_keys)
    if not isinstance(stripped, dict):
        return {}
    allowed_keys = set(_SAFE_MANAGED_TASK_CONTENT_KEYS)
    if task_type in ("QUIZ", "FORM"):
        allowed_keys.add("questions")
    if keep_answer_keys:
        allowed_keys.update({"correct_value", "correct_answers"})
    return {k: v for k, v in stripped.items() if k in allowed_keys}


async def _project_managed_linked_assignment(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    release: StudyCourseRelease,
    act_row: dict[str, Any],
    asg_row: dict[str, Any],
    retained_tasks: list[dict[str, Any]],
) -> ManagedLinkedAssignment:
    from src.db.courses.assignments import Assignment, AssignmentTaskSubmission, AssignmentUserSubmission
    from src.services.studytomy.assessment_reviews import resolve_assessment_retry_grant
    from src.services.studytomy.grading import latest_assessment_result, submission_contract_event

    user_sub = (
        await db.execute(
            select(AssignmentUserSubmission).where(
                AssignmentUserSubmission.assignment_id == asg_row["id"],
                AssignmentUserSubmission.user_id == actor.id,
            )
        )
    ).scalar_one_or_none()
    live_asg = await db.get(Assignment, asg_row["id"])
    bound_release_uuid: str | None = None
    if user_sub is not None and live_asg is not None:
        contract = await submission_contract_event(db, live_asg, user_sub)
        if contract is not None and isinstance(contract.after_state, dict):
            raw_bound = contract.after_state.get("assessment_release_uuid")
            if isinstance(raw_bound, str):
                bound_release_uuid = raw_bound

    release_conflict_uuid = (
        bound_release_uuid
        if bound_release_uuid is not None and bound_release_uuid != release.uuid
        else None
    )
    visible_user_sub = None if release_conflict_uuid is not None else user_sub

    task_sub_by_id: dict[int, AssignmentTaskSubmission] = {}
    if release_conflict_uuid is None:
        task_subs = (
            await db.execute(
                select(AssignmentTaskSubmission).where(
                    AssignmentTaskSubmission.activity_id == act_row["id"],
                    AssignmentTaskSubmission.user_id == actor.id,
                )
            )
        ).scalars().all()
        task_sub_by_id = {sub.assignment_task_id: sub for sub in task_subs}

    status_value: str | None = None
    if visible_user_sub is not None:
        raw_status = visible_user_sub.submission_status
        status_value = raw_status.value if hasattr(raw_status, "value") else str(raw_status)
        if status_value.startswith("AssignmentUserSubmissionStatus."):
            status_value = status_value.split(".", 1)[1]

    is_graded = visible_user_sub is not None and status_value == "GRADED"
    show_keys = bool(is_graded and asg_row.get("show_correct_answers"))
    max_grade = sum(int(row.get("max_grade_value") or 0) for row in retained_tasks)

    verified_score = visible_user_sub.grade if is_graded else None
    if is_graded and live_asg is not None and visible_user_sub is not None:
        recorded_result = await latest_assessment_result(db, live_asg, visible_user_sub)
        if recorded_result is not None and isinstance(recorded_result.after_state, dict):
            score_val = recorded_result.after_state.get("score_points")
            if isinstance(score_val, int):
                verified_score = score_val

    past_due, effective_due = await _managed_assignment_deadline_state(
        db, org.id, asg_row["id"], asg_row, actor.id
    )
    editable_status = status_value in (None, "PENDING", "NOT_SUBMITTED")
    can_submit = (
        release_conflict_uuid is None
        and editable_status
        and not past_due
        and bool(retained_tasks)
    )

    allow_retries = bool(asg_row.get("allow_retries", False))
    max_retries = int(asg_row.get("max_retries", 0) or 0)
    can_retry = False
    if release_conflict_uuid is None and visible_user_sub is not None and live_asg is not None:
        retryable = (
            status_value in ("SUBMITTED", "GRADED")
            if asg_row.get("ungraded")
            else status_value == "GRADED"
        )
        if retryable:
            grant = await resolve_assessment_retry_grant(db, live_asg, visible_user_sub)
            if grant is not None:
                can_retry = True
            elif allow_retries and not past_due:
                current_attempt = int(visible_user_sub.attempt_number or 1)
                can_retry = (max_retries == 0) or (current_attempt < max_retries)

    task_summaries: list[ManagedAssignmentTaskSummary] = []
    for task_row in retained_tasks:
        t_sub = task_sub_by_id.get(task_row.get("id"))
        t_type = str(task_row.get("assignment_type") or "FILE_SUBMISSION")
        sanitized_contents = _sanitize_managed_task_contents(
            task_row.get("contents"), t_type, show_keys
        )
        task_summaries.append(
            ManagedAssignmentTaskSummary(
                assignment_task_uuid=str(task_row["assignment_task_uuid"]),
                title=str(task_row.get("title") or "Task"),
                description=str(task_row.get("description") or ""),
                assignment_type=t_type,
                max_grade_value=task_row.get("max_grade_value"),
                student_grade=t_sub.grade if (is_graded and t_sub is not None) else None,
                teacher_feedback=(
                    t_sub.task_submission_grade_feedback
                    if (is_graded and t_sub is not None and t_sub.task_submission_grade_feedback)
                    else None
                ),
                contents=sanitized_contents,
                submitted_answer=dict(t_sub.task_submission) if (t_sub is not None and isinstance(t_sub.task_submission, dict)) else None,
            )
        )

    return ManagedLinkedAssignment(
        course_uuid=release.course_uuid,
        release_uuid=release.uuid,
        name=str(act_row.get("name") or asg_row.get("title") or "Practical Assignment"),
        activity_uuid=str(act_row["activity_uuid"]),
        assignment_uuid=str(asg_row["assignment_uuid"]),
        submission_uuid=visible_user_sub.assignmentusersubmission_uuid if visible_user_sub is not None else None,
        title=str(asg_row.get("title") or act_row.get("name") or "Practical Assignment"),
        description=str(asg_row.get("description") or ""),
        due_date=effective_due,
        grading_type=str(asg_row.get("grading_type") or "ALPHABET"),
        submission_status=status_value,
        grade=verified_score,
        max_grade=max_grade,
        overall_feedback=visible_user_sub.overall_feedback if is_graded else None,
        attempt_number=visible_user_sub.attempt_number if visible_user_sub is not None else None,
        past_due=past_due,
        can_submit=can_submit,
        can_retry=can_retry,
        allow_retries=allow_retries,
        max_retries=max_retries,
        release_conflict_uuid=release_conflict_uuid,
        tasks=task_summaries,
    )


async def read_managed_linked_assignment(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    release_uuid: str,
    activity_uuid: str,
) -> ManagedLinkedAssignment:
    """Authorize and project a retained assignment linked to an approved managed lab (T-08)."""
    release, _, act_row, asg_row, retained_tasks = await _resolve_managed_assignment_target(
        db, org, actor, course_uuid, release_uuid, activity_uuid
    )
    return await _project_managed_linked_assignment(
        db, org, actor, release, act_row, asg_row, retained_tasks
    )


async def _ensure_managed_round_writable(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    release: StudyCourseRelease,
    asg_row: dict[str, Any],
) -> tuple[Any, Any, Any]:
    from src.db.courses.activities import Activity
    from src.db.courses.assignments import Assignment, AssignmentUserSubmissionStatus
    from src.services.studytomy.grading import lock_or_reserve_native_round, submission_contract_event

    course = (
        await db.execute(
            select(Course).where(Course.org_id == org.id, Course.course_uuid == release.course_uuid)
        )
    ).scalar_one_or_none()
    assignment = await db.get(Assignment, asg_row["id"])
    activity = await db.get(Activity, asg_row["activity_id"])
    if (
        course is None
        or course.studytomy_managed is not True
        or assignment is None
        or assignment.org_id != org.id
        or assignment.assignment_uuid != asg_row.get("assignment_uuid")
        or activity is None
    ):
        raise _unavailable()

    attempt = await lock_or_reserve_native_round(db, assignment, actor.id)
    bound_contract = await submission_contract_event(db, assignment, attempt)
    if bound_contract is not None and isinstance(bound_contract.after_state, dict):
        bound_uuid = bound_contract.after_state.get("assessment_release_uuid")
        if isinstance(bound_uuid, str) and bound_uuid != release.uuid:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "MANAGED_ASSESSMENT_RELEASE_CONFLICT",
                    "message": "Your existing attempt on this assignment is bound to another course release.",
                },
            )
    if attempt.submission_status not in (
        AssignmentUserSubmissionStatus.PENDING,
        AssignmentUserSubmissionStatus.NOT_SUBMITTED,
    ):
        raise HTTPException(
            status_code=409,
            detail="This assignment has already been handed in. Use retry to attempt it again.",
        )
    past_due, _ = await _managed_assignment_deadline_state(db, org.id, assignment.id, asg_row, actor.id)
    if past_due:
        raise HTTPException(status_code=403, detail="Assignment deadline has passed")
    return course, assignment, attempt


def _validate_managed_task_payload(
    task_row: dict[str, Any],
    task_submission: dict[str, Any],
    actor_id: int,
) -> dict[str, Any]:
    from src.db.courses.assignments import AssignmentTaskTypeEnum
    from src.security.submission_file_access import submission_file_owner, submission_files_belong_to
    from src.services.courses.activities.assignments import _check_number_answer

    task_type = str(task_row.get("assignment_type") or "")
    if not isinstance(task_submission, dict):
        raise HTTPException(status_code=422, detail="Submission payload must be an object.")

    if task_type == AssignmentTaskTypeEnum.NUMBER_ANSWER.value:
        if set(task_submission.keys()) - {"answer"}:
            raise HTTPException(status_code=422, detail="Unexpected fields in numeric answer submission.")
        raw_answer = task_submission.get("answer")
        if isinstance(raw_answer, bool) or not isinstance(raw_answer, (str, int, float)):
            raise HTTPException(status_code=422, detail="Numeric answer must be a finite number.")
        text = str(raw_answer).strip()
        if not text or len(text) > 64:
            raise HTTPException(status_code=422, detail="Enter a numeric answer (up to 64 characters).")
        # Reuse the normalized finite-number parser from _check_number_answer via tolerance check on non-NaN parsing.
        if not _check_number_answer(text, 0, float("inf")):
            raise HTTPException(status_code=422, detail="Enter a valid finite numeric value.")
        return {"answer": text}

    if task_type == AssignmentTaskTypeEnum.FILE_SUBMISSION.value:
        if set(task_submission.keys()) - {"fileUUID"}:
            raise HTTPException(status_code=422, detail="Unexpected fields in file submission.")
        file_uuid = task_submission.get("fileUUID")
        if (
            not isinstance(file_uuid, str)
            or not file_uuid.strip()
            or file_uuid != file_uuid.strip()
            or len(file_uuid) > 255
            or "/" in file_uuid
            or "\\" in file_uuid
            or ".." in file_uuid
            or "\x00" in file_uuid
        ):
            raise HTTPException(status_code=422, detail="A valid uploaded file identifier is required.")
        if not submission_files_belong_to(task_submission, actor_id) or submission_file_owner(file_uuid) != int(actor_id):
            raise HTTPException(status_code=403, detail="You may only attach your own uploaded submission file.")
        if file_uuid.lower().endswith(".csv"):
            raise HTTPException(status_code=415, detail="Direct CSV uploads are not accepted; package exported CSVs in a ZIP or submit a PDF.")
        return {"fileUUID": file_uuid}

    raise HTTPException(status_code=422, detail="This task type is not supported for managed online submission.")


async def upload_managed_assignment_task_file(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    release_uuid: str,
    activity_uuid: str,
    task_uuid: str,
    sub_file: UploadFile,
    *,
    idempotency_key: str | None = None,
) -> ManagedTaskFileUploadReceipt:
    from src.db.courses.assignments import AssignmentTaskTypeEnum
    from src.services.courses.activities.uploads.sub_file import upload_submission_file
    from src.services.studytomy.idempotency import claim_idempotency, complete_idempotency

    org = await lock_organization(db, org.id)
    release, _, act_row, asg_row, retained_tasks = await _resolve_managed_assignment_target(
        db, org, actor, course_uuid, release_uuid, activity_uuid
    )
    task_row = next((row for row in retained_tasks if row.get("assignment_task_uuid") == task_uuid), None)
    if task_row is None:
        raise HTTPException(status_code=404, detail="Assignment task not found")
    if str(task_row.get("assignment_type")) != AssignmentTaskTypeEnum.FILE_SUBMISSION.value:
        raise HTTPException(status_code=422, detail="Files can only be uploaded for file-submission tasks.")

    replay = await claim_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation="studytomy.managed_assignment_upload",
        idempotency_key=idempotency_key,
        request_payload={
            "course_uuid": course_uuid,
            "release_uuid": release_uuid,
            "activity_uuid": activity_uuid,
            "task_uuid": task_uuid,
            "filename": sub_file.filename or "",
        },
    )
    if replay is not None and isinstance(replay.response_projection, dict):
        return ManagedTaskFileUploadReceipt.model_validate(replay.response_projection)

    await _ensure_managed_round_writable(db, org, actor, release, asg_row)
    if not sub_file or not sub_file.filename:
        raise HTTPException(status_code=422, detail="A submission file is required.")

    file_uuid = await upload_submission_file(
        sub_file,
        str(act_row["activity_uuid"]),
        org.org_uuid,
        course_uuid,
        str(asg_row["assignment_uuid"]),
        task_uuid,
        actor.id,
    )
    receipt = ManagedTaskFileUploadReceipt(file_uuid=file_uuid, assignment_task_uuid=task_uuid)
    await complete_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation="studytomy.managed_assignment_upload",
        idempotency_key=idempotency_key,
        resource_kind="managed_assignment_file",
        resource_uuid=file_uuid,
        response_status=200,
        response_projection=receipt.model_dump(mode="json"),
    )
    return receipt


async def save_managed_assignment_task_draft(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    release_uuid: str,
    activity_uuid: str,
    task_uuid: str,
    task_submission_payload: dict[str, Any],
    *,
    idempotency_key: str | None = None,
) -> ManagedLinkedAssignment:
    from uuid import uuid4
    from src.db.courses.assignments import AssignmentTask, AssignmentTaskSubmission, AssignmentTaskTypeEnum
    from src.services.studytomy.grading import record_grading_contract
    from src.services.studytomy.idempotency import claim_idempotency, complete_idempotency

    org = await lock_organization(db, org.id)
    release, _, act_row, asg_row, retained_tasks = await _resolve_managed_assignment_target(
        db, org, actor, course_uuid, release_uuid, activity_uuid
    )
    task_row = next((row for row in retained_tasks if row.get("assignment_task_uuid") == task_uuid), None)
    if task_row is None:
        raise HTTPException(status_code=404, detail="Assignment task not found")
    cleaned_payload = _validate_managed_task_payload(task_row, task_submission_payload, actor.id)

    replay = await claim_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation="studytomy.managed_assignment_draft",
        idempotency_key=idempotency_key,
        request_payload={
            "course_uuid": course_uuid,
            "release_uuid": release_uuid,
            "activity_uuid": activity_uuid,
            "task_uuid": task_uuid,
            "task_submission": cleaned_payload,
        },
    )
    if replay is not None and isinstance(replay.response_projection, dict):
        return ManagedLinkedAssignment.model_validate(replay.response_projection)

    _, assignment, attempt = await _ensure_managed_round_writable(db, org, actor, release, asg_row)
    live_task = await db.get(AssignmentTask, task_row["id"])
    if live_task is None or live_task.assignment_id != assignment.id:
        raise _unavailable()

    if not asg_row.get("ungraded"):
        await record_grading_contract(db, assignment, attempt, actor, issued=True, release=release)

    now_str = str(datetime.now())
    existing_ts = (
        await db.execute(
            select(AssignmentTaskSubmission)
            .where(
                AssignmentTaskSubmission.assignment_task_id == live_task.id,
                AssignmentTaskSubmission.user_id == actor.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if existing_ts is not None:
        existing_ts.task_submission = cleaned_payload
        existing_ts.grade = 0
        existing_ts.task_submission_grade_feedback = ""
        existing_ts.manually_graded = False
        existing_ts.update_date = now_str
        db.add(existing_ts)
    else:
        db.add(
            AssignmentTaskSubmission(
                assignment_task_submission_uuid=f"assignmenttasksubmission_{uuid4()}",
                task_submission=cleaned_payload,
                grade=0,
                task_submission_grade_feedback="",
                manually_graded=False,
                assignment_task_id=live_task.id,
                assignment_type=AssignmentTaskTypeEnum(task_row["assignment_type"]),
                activity_id=act_row["id"],
                course_id=asg_row["course_id"],
                chapter_id=asg_row["chapter_id"],
                user_id=actor.id,
                creation_date=now_str,
                update_date=now_str,
            )
        )
    await db.flush()
    projection = await _project_managed_linked_assignment(
        db, org, actor, release, act_row, asg_row, retained_tasks
    )
    await complete_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation="studytomy.managed_assignment_draft",
        idempotency_key=idempotency_key,
        resource_kind="managed_assignment_draft",
        resource_uuid=attempt.assignmentusersubmission_uuid,
        response_status=200,
        response_projection=projection.model_dump(mode="json"),
    )
    return projection


async def submit_managed_linked_assignment(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    release_uuid: str,
    activity_uuid: str,
    *,
    idempotency_key: str | None = None,
) -> ManagedLinkedAssignment:
    from src.db.courses.assignments import (
        AssignmentTaskSubmission,
        AssignmentUserSubmissionStatus,
    )
    from src.db.trail_steps import TrailStep
    from src.db.trails import Trail
    from src.services.courses.activities.assignments import (
        AUTO_GRADABLE_TASK_TYPES,
        _apply_grade_and_finalize,
    )
    from src.services.studytomy.assignment_adapter import freeze_native_assignment_answers
    from src.services.studytomy.grading import (
        record_assessment_responses,
        record_grading_contract,
        release_assignment_scope,
    )
    from src.services.studytomy.idempotency import claim_idempotency, complete_idempotency

    org = await lock_organization(db, org.id)
    release, _, act_row, asg_row, retained_tasks = await _resolve_managed_assignment_target(
        db, org, actor, course_uuid, release_uuid, activity_uuid
    )
    if not retained_tasks:
        raise HTTPException(status_code=422, detail="This assignment has no retained tasks to submit.")

    replay = await claim_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation="studytomy.managed_assignment_submit",
        idempotency_key=idempotency_key,
        request_payload={
            "course_uuid": course_uuid,
            "release_uuid": release_uuid,
            "activity_uuid": activity_uuid,
        },
    )
    if replay is not None and isinstance(replay.response_projection, dict):
        return ManagedLinkedAssignment.model_validate(replay.response_projection)

    course, assignment, attempt = await _ensure_managed_round_writable(db, org, actor, release, asg_row)

    task_ids = [int(row["id"]) for row in retained_tasks if row.get("id") is not None]
    task_subs = (
        await db.execute(
            select(AssignmentTaskSubmission).where(
                AssignmentTaskSubmission.user_id == actor.id,
                AssignmentTaskSubmission.assignment_task_id.in_(task_ids),
            )
        )
    ).scalars().all()
    sub_by_task_id = {sub.assignment_task_id: sub for sub in task_subs}
    for task_row in retained_tasks:
        t_sub = sub_by_task_id.get(task_row.get("id"))
        if t_sub is None or not isinstance(t_sub.task_submission, dict) or not t_sub.task_submission:
            raise HTTPException(
                status_code=422,
                detail=f"Complete task '{task_row.get('title') or task_row.get('assignment_task_uuid')}' before submitting.",
            )
        cleaned = _validate_managed_task_payload(task_row, t_sub.task_submission, actor.id)
        t_sub.task_submission = cleaned
        t_sub.grade = 0
        t_sub.task_submission_grade_feedback = ""
        t_sub.manually_graded = False
        db.add(t_sub)

    await freeze_native_assignment_answers(db, assignment, attempt, direct_learner=True)
    now_str = str(datetime.now())
    attempt.submission_status = AssignmentUserSubmissionStatus.SUBMITTED
    attempt.grade = 0
    attempt.creation_date = now_str
    attempt.update_date = now_str
    db.add(attempt)

    eff_assignment, eff_tasks, _ = release_assignment_scope(assignment, release)
    if not eff_assignment.ungraded:
        await record_grading_contract(db, assignment, attempt, actor, issued=True, release=release)
    await record_assessment_responses(db, assignment, attempt, actor)

    # Record trail step completion on assignment hand-in without touching lab or mastery credit.
    trail = (
        await db.execute(
            select(Trail).where(Trail.org_id == org.id, Trail.user_id == actor.id)
        )
    ).scalar_one_or_none()
    if trail is None:
        trail = Trail(
            org_id=org.id,
            user_id=actor.id,
            trail_uuid=f"trail_{org.id}_{actor.id}",
            creation_date=now_str,
            update_date=now_str,
        )
        db.add(trail)
        await db.flush()
    trailrun = (
        await db.execute(
            select(TrailRun).where(
                TrailRun.trail_id == trail.id,
                TrailRun.course_id == course.id,
                TrailRun.user_id == actor.id,
            )
        )
    ).scalar_one_or_none()
    if trailrun is None:
        trailrun = TrailRun(
            trail_id=trail.id,
            course_id=course.id,
            org_id=org.id,
            user_id=actor.id,
            status=StatusEnum.STATUS_IN_PROGRESS,
            creation_date=now_str,
            update_date=now_str,
        )
        db.add(trailrun)
        await db.flush()
    trailstep = (
        await db.execute(
            select(TrailStep).where(
                TrailStep.trailrun_id == trailrun.id,
                TrailStep.activity_id == act_row["id"],
                TrailStep.user_id == actor.id,
            )
        )
    ).scalar_one_or_none()
    if trailstep is None:
        db.add(
            TrailStep(
                trailrun_id=trailrun.id,
                activity_id=act_row["id"],
                course_id=course.id,
                trail_id=trail.id,
                org_id=org.id,
                complete=True,
                teacher_verified=False,
                grade="",
                user_id=actor.id,
                creation_date=now_str,
                update_date=now_str,
            )
        )
    else:
        trailstep.complete = True
        trailstep.update_date = now_str
        db.add(trailstep)
    await db.flush()

    all_auto_gradable = all(t.assignment_type in AUTO_GRADABLE_TASK_TYPES for t in eff_tasks)
    if eff_assignment.auto_grading and not eff_assignment.ungraded and all_auto_gradable:
        await _apply_grade_and_finalize(
            assignment=assignment,
            course=course,
            user_id=actor.id,
            assignment_user_submission=attempt,
            db_session=db,
            overall_feedback=None,
            auto_graded=True,
            assignment_tasks=eff_tasks,
        )

    projection = await _project_managed_linked_assignment(
        db, org, actor, release, act_row, asg_row, retained_tasks
    )
    await complete_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation="studytomy.managed_assignment_submit",
        idempotency_key=idempotency_key,
        resource_kind="managed_assignment_submission",
        resource_uuid=attempt.assignmentusersubmission_uuid,
        response_status=200,
        response_projection=projection.model_dump(mode="json"),
    )
    return projection


async def retry_managed_linked_assignment(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    release_uuid: str,
    activity_uuid: str,
) -> ManagedLinkedAssignment:
    from src.db.courses.assignments import (
        Assignment,
        AssignmentTask,
        AssignmentTaskSubmission,
        AssignmentUserSubmission,
        AssignmentUserSubmissionStatus,
    )
    from src.db.trail_steps import TrailStep
    from src.services.studytomy.assessment_reviews import resolve_assessment_retry_grant
    from src.services.studytomy.assignment_adapter import ensure_native_retry_round_frozen
    from src.services.studytomy.grading import (
        archive_assessment_responses_before_retry,
        submission_contract_event,
    )

    org = await lock_organization(db, org.id)
    release, _, act_row, asg_row, retained_tasks = await _resolve_managed_assignment_target(
        db, org, actor, course_uuid, release_uuid, activity_uuid
    )
    assignment = await db.get(Assignment, asg_row["id"])
    if assignment is None or assignment.org_id != org.id:
        raise _unavailable()
    attempt = (
        await db.execute(
            select(AssignmentUserSubmission)
            .where(
                AssignmentUserSubmission.assignment_id == assignment.id,
                AssignmentUserSubmission.user_id == actor.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if attempt is None:
        raise HTTPException(status_code=404, detail="Assignment User Submission not found")
    bound_contract = await submission_contract_event(db, assignment, attempt)
    if bound_contract is not None and isinstance(bound_contract.after_state, dict):
        bound_uuid = bound_contract.after_state.get("assessment_release_uuid")
        if isinstance(bound_uuid, str) and bound_uuid != release.uuid:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "MANAGED_ASSESSMENT_RELEASE_CONFLICT",
                    "message": "Your existing attempt on this assignment is bound to another course release.",
                },
            )
    ungraded = bool(asg_row.get("ungraded", False))
    allowed_statuses = (
        (AssignmentUserSubmissionStatus.SUBMITTED, AssignmentUserSubmissionStatus.GRADED)
        if ungraded
        else (AssignmentUserSubmissionStatus.GRADED,)
    )
    if attempt.submission_status not in allowed_statuses:
        raise HTTPException(
            status_code=400,
            detail="Only submitted assignments can be retried" if ungraded else "Only graded submissions can be retried",
        )
    grant = await resolve_assessment_retry_grant(db, assignment, attempt)
    if grant is None and not bool(asg_row.get("allow_retries", False)):
        raise HTTPException(status_code=403, detail="Retries are not enabled for this assignment")
    past_due, _ = await _managed_assignment_deadline_state(db, org.id, assignment.id, asg_row, actor.id)
    if grant is None and past_due:
        raise HTTPException(status_code=403, detail="Assignment deadline has passed")
    current_attempt = int(attempt.attempt_number or 1)
    max_attempts = int(asg_row.get("max_retries", 0) or 0)
    if grant is None and max_attempts and current_attempt >= max_attempts:
        raise HTTPException(status_code=403, detail="No retry attempts remaining")

    await ensure_native_retry_round_frozen(db, assignment, attempt)
    await archive_assessment_responses_before_retry(db, assignment, attempt)

    task_ids = [
        tid
        for tid in (
            await db.execute(select(AssignmentTask.id).where(AssignmentTask.assignment_id == assignment.id))
        ).scalars().all()
        if tid is not None
    ]
    if task_ids:
        existing_subs = (
            await db.execute(
                select(AssignmentTaskSubmission).where(
                    AssignmentTaskSubmission.user_id == actor.id,
                    AssignmentTaskSubmission.assignment_task_id.in_(task_ids),
                )
            )
        ).scalars().all()
        for ts in existing_subs:
            await db.delete(ts)

    trailstep = (
        await db.execute(
            select(TrailStep).where(
                TrailStep.activity_id == assignment.activity_id,
                TrailStep.user_id == actor.id,
            )
        )
    ).scalar_one_or_none()
    now_str = str(datetime.now())
    if trailstep is not None:
        trailstep.complete = False
        trailstep.teacher_verified = False
        trailstep.grade = ""
        trailstep.update_date = now_str
        db.add(trailstep)

    attempt.submission_status = AssignmentUserSubmissionStatus.PENDING
    attempt.grade = 0
    attempt.overall_feedback = None
    attempt.attempt_number = current_attempt + 1
    attempt.update_date = now_str
    db.add(attempt)
    await db.flush()
    return await _project_managed_linked_assignment(
        db, org, actor, release, act_row, asg_row, retained_tasks
    )


async def read_managed_submission_file_download(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
    release_uuid: str,
    activity_uuid: str,
    task_uuid: str,
    filename: str,
) -> ManagedSubmissionDownload:
    from src.db.courses.assignments import AssignmentTaskSubmission
    from src.security.submission_file_access import _submission_references_file, submission_file_owner

    if (
        not filename
        or "/" in filename
        or "\\" in filename
        or ".." in filename
        or "\x00" in filename
    ):
        raise HTTPException(status_code=400, detail="Invalid file name")

    _, _, act_row, asg_row, retained_tasks = await _resolve_managed_assignment_target(
        db, org, actor, course_uuid, release_uuid, activity_uuid
    )
    task_row = next((row for row in retained_tasks if row.get("assignment_task_uuid") == task_uuid), None)
    if task_row is None:
        raise HTTPException(status_code=404, detail="Assignment task not found")
    owner = submission_file_owner(filename)
    if owner is not None and owner != int(actor.id):
        raise HTTPException(status_code=403, detail="Access denied")

    t_sub = (
        await db.execute(
            select(AssignmentTaskSubmission).where(
                AssignmentTaskSubmission.assignment_task_id == task_row["id"],
                AssignmentTaskSubmission.user_id == actor.id,
            )
        )
    ).scalar_one_or_none()
    if t_sub is None or not _submission_references_file(t_sub.task_submission, filename):
        raise HTTPException(status_code=404, detail="Submission file not found")

    ext = PurePosixPath(filename).suffix.lower()
    media_type = {
        ".pdf": "application/pdf",
        ".zip": "application/zip",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }.get(ext, "application/octet-stream")
    directory = (
        f"courses/{course_uuid}/activities/{act_row['activity_uuid']}/assignments/{asg_row['assignment_uuid']}/tasks/{task_uuid}/subs"
    )
    return ManagedSubmissionDownload(
        filename=filename,
        media_type=media_type,
        directory=directory,
        org_uuid=org.org_uuid,
    )
