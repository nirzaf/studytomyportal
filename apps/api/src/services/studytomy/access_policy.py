"""Self-service access to reviewed managed course releases."""

from datetime import UTC, datetime, timedelta
from typing import Literal

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
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization


class ManagedReleaseOverview(BaseModel):
    course_uuid: str
    release_uuid: str
    name: str


class ManagedTextLesson(ManagedReleaseOverview):
    activity_uuid: str
    paragraphs: list[str]


class ManagedReleaseCatalog(BaseModel):
    releases: list[ManagedReleaseOverview]
    next_cursor: str | None


class ManagedLessonSummary(BaseModel):
    activity_uuid: str
    name: str


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
) -> tuple[StudyLearnerProfile, StudyPolicyVersion]:
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
    return profile, progression


def _reviewed_releases(org_id: int):
    return (
        select(StudyCourseRelease)
        .join(Course, (Course.org_id == StudyCourseRelease.org_id)
              & (Course.course_uuid == StudyCourseRelease.course_uuid))
        .join(StudyCourseReview, StudyCourseReview.id == StudyCourseRelease.review_id)
        .where(
            StudyCourseRelease.org_id == org_id, Course.studytomy_managed.is_(True),
            StudyCourseRelease.policy_binding.is_not(None),
            StudyCourseReview.org_id == org_id,
            StudyCourseReview.course_uuid == StudyCourseRelease.course_uuid,
            StudyCourseReview.decision == "approved",
            StudyCourseReview.revision_sha256 == StudyCourseRelease.snapshot_sha256,
        )
    )


async def _authorized_release(
    db: AsyncSession, org: Organization, actor: PublicUser, course_uuid: str, release_uuid: str,
    *, require_active_grant: bool = True,
) -> tuple[StudyCourseRelease, StudyPolicyVersion, int]:
    now = datetime.now(UTC)
    profile, progression = await _authorized_learner(db, org, actor, now)
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
    profile, _ = await _authorized_learner(db, org, actor, now)
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
    profile, _ = await _authorized_learner(db, org, actor, now)
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
    lessons = []
    for row in sorted(rows, key=lambda item: positions[item["id"]]):
        try:
            lesson = _project_text_lesson(release, policy, row["activity_uuid"])
        except HTTPException as error:
            if error.status_code != 404:
                raise
            continue
        lessons.append(ManagedLessonSummary(activity_uuid=lesson.activity_uuid, name=lesson.name))
    return ManagedLessonIndex(
        requires_first_use=all(grant.starts_at is None for grant in grants), lessons=lessons,
    )
