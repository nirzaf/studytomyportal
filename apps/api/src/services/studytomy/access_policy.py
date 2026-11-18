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
from typing import BinaryIO, Literal

from fastapi import HTTPException
from pydantic import BaseModel
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
from src.services.studytomy.releases import (
    _NATIVE_ACTIVITY_MEDIA,
    _RELEASE_MEDIA_TYPES,
)
from src.services.utils.upload_content import open_content_stream

_MAX_RELEASE_MEDIA_BYTES = 500 * 1024 * 1024
_RELEASE_MEDIA_STAGE_TIMEOUT_SECONDS = 300
_RELEASE_MEDIA_FILENAME = {
    "application/pdf": "lesson.pdf",
    "video/mp4": "lesson.mp4",
    "video/webm": "lesson.webm",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ManagedReleaseOverview(BaseModel):
    course_uuid: str
    release_uuid: str
    name: str


class ManagedTextLesson(ManagedReleaseOverview):
    activity_uuid: str
    paragraphs: list[str]


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
    format: Literal["text", "pdf", "video", "unsupported"]
    media_type: str | None
    available: bool
    unavailable_reason: Literal[
        "release_content_unavailable",
        "release_media_unavailable",
        "unsupported_release_format",
        "release_format_conflict",
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
        StudyLearnerProfile.status == "active", StudyLearnerProfile.control_mode == "independent",
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
    if recorded_at.tzinfo is not None and now - timedelta(days=days) <= recorded_at < starts_at and recorded_at <= now:
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


def _project_text_lesson(
    release: StudyCourseRelease, policy: StudyPolicyVersion, activity_uuid: str,
) -> ManagedTextLesson:
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_09, policy.body)
    schedule = body.specified_value("release_schedule") if body else None
    allowed = schedule.get("ungated_text_activities") if isinstance(schedule, dict) else None
    activities = allowed.get(release.course_uuid) if isinstance(allowed, dict) else None
    if policy.id != release.progression_policy_id or not isinstance(activities, list) or activity_uuid not in activities:
        raise _unavailable()
    snapshot = release.snapshot
    course = snapshot.get("course")
    if not isinstance(course, dict) or course.get("drip_settings") or snapshot.get("schema") != "course_content_evidence_v4":
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
        or chapters[0].get("drip_rule")
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
) -> tuple[str, dict, str]:
    snapshot = release.snapshot
    course = snapshot.get("course")
    if (
        not isinstance(course, dict)
        or course.get("drip_settings")
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
        or chapters[0].get("drip_rule")
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
        or any(blockers.values())
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
    media = release.media_manifest.get(source_reference)
    if (
        not isinstance(media, dict)
        or media.get("media_type") != media_type
        or not _SHA256.fullmatch(str(media.get("sha256", "")))
        or type(media.get("size_bytes")) is not int
        or media["size_bytes"] < 1
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
) -> None:
    available, reason = _managed_media_availability(policy, media_type, size_bytes)
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
        include_unstarted=True,
    )
    if not candidates:
        raise _unavailable()
    source_reference, media, media_type = _project_media_activity(
        release, progression, activity_uuid
    )
    policy = (await _current_policies(
        db, org, (StudyPolicyKey.D_15,), datetime.now(UTC)
    ))[0]
    _validate_media_delivery_policy(policy, media_type, media["size_bytes"])
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
        current_source_reference, current_media, current_media_type = _project_media_activity(
            current_release, current_progression, activity_uuid
        )
        if (
            current_source_reference != source_reference
            or current_media_type != media_type
            or current_media != media_snapshot
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
        _validate_media_delivery_policy(
            current_policy, media_type, media_snapshot["size_bytes"]
        )

        now = datetime.now(UTC)
        grants = await resolve_active_course_grants(
            db,
            org_id=org_id,
            learner_profile_id=profile_id,
            course_uuid=course_uuid,
            release_id=release_id,
            now=now,
            claim_first_use=start_first_use,
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
    org = await lock_organization(db, org.id)
    release, policy, profile_id = await _authorized_release(
        db, org, actor, course_uuid, release_uuid, require_active_grant=False,
    )
    result = _project_text_lesson(release, policy, activity_uuid)
    now = datetime.now(UTC)
    grants = await resolve_active_course_grants(
        db, org_id=org.id, learner_profile_id=profile_id, course_uuid=course_uuid,
        release_id=release.id, now=now, claim_first_use=start_first_use,
    )
    if not grants:
        raise _unavailable()
    await _retain_allocation_delivery(db, org, actor, grants, now)
    return result


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

    # The current immutable release format validator does not retain video
    # captions or an explicit accessible alternative. Keep the item visible in
    # its approved position, but do not advertise playback as available.
    if media_type.startswith("video/"):
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
    text_ids = text_schedule.get(course_uuid) if isinstance(text_schedule, dict) else None
    media_ids = media_schedule.get(course_uuid) if isinstance(media_schedule, dict) else None
    authorized_text = set(text_ids) if isinstance(text_ids, list) and all(
        isinstance(value, str) for value in text_ids
    ) else set()
    authorized_media = set(media_ids) if isinstance(media_ids, list) and all(
        isinstance(value, str) for value in media_ids
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
        if not is_text and not is_media:
            continue

        name = row.get("name")
        name = name.strip() if isinstance(name, str) else ""
        if not name:
            name = "Unavailable activity"
        position = len(lessons) + 1

        if is_text and is_media:
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

        if is_text:
            try:
                lesson = _project_text_lesson(release, policy, activity_uuid)
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
                    unavailable_reason="release_content_unavailable",
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
                release, policy, activity_uuid
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
                    "release_media_unavailable"
                    if media_format != "unsupported"
                    else "unsupported_release_format"
                ),
            ))
            continue
        media_format = "pdf" if media_type == "application/pdf" else "video"
        available, unavailable_reason = _managed_media_availability(
            media_policy, media_type, media["size_bytes"]
        )
        lessons.append(ManagedLessonSummary(
            activity_uuid=activity_uuid,
            name=name,
            position=position,
            format=media_format,
            media_type=media_type,
            available=available,
            unavailable_reason=unavailable_reason,
        ))
    return ManagedLessonIndex(
        requires_first_use=all(grant.starts_at is None for grant in grants), lessons=lessons,
    )
