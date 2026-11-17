"""Evaluate grants for a learner and one exact, authorized release."""

from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.access import StudyCourseAccessGrant
from src.db.studytomy.governance import (
    StudyPolicyKey,
    StudyPolicyVersion,
    inspect_study_policy_body,
)
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.learners import _active_policy
from src.services.studytomy.policies import lock_organization


async def current_allocation_delivery_policy(
    db: AsyncSession, org: Organization, now: datetime,
) -> StudyPolicyVersion | None:
    """Resolve explicit permission to retain first content delivery for allocation review."""
    try:
        policy = await _active_policy(db, org, StudyPolicyKey.D_12, now)
    except HTTPException as error:
        if error.status_code != 409:
            raise
        return None
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_12, policy.body)
    uses = body.specified_value("information_uses") if body else None
    scopes = uses.get("allocation_correction") if isinstance(uses, dict) else None
    return policy if isinstance(scopes, list) and "first_content_delivery" in scopes else None


async def persist_authorized_course_grant(
    db: AsyncSession, candidate: StudyCourseAccessGrant,
) -> StudyCourseAccessGrant:
    """Record a grant after its source has been verified in this transaction.

    The caller owns source verification, policy and learner checks, audit
    events, and commit. This function is internal to the commerce service.
    """
    await lock_organization(db, candidate.org_id)
    grant = StudyCourseAccessGrant
    existing = (await db.execute(select(grant).where(
        grant.org_id == candidate.org_id,
        grant.learner_profile_id == candidate.learner_profile_id,
        grant.course_uuid == candidate.course_uuid,
        grant.source_kind == candidate.source_kind,
        grant.source_uuid == candidate.source_uuid,
    ).execution_options(populate_existing=True))).scalar_one_or_none()

    if candidate.start_basis == "after_first_access" and (
        candidate.starts_at is not None or candidate.ends_at is not None
    ):
        raise ValueError("A new first-use grant must have an unclaimed window")
    if candidate.id is not None or candidate.revoked_at is not None:
        raise ValueError("A grant candidate must be new and unrevoked")

    if existing is None:
        db.add(candidate)
        await db.flush()
        return candidate

    if (
        existing.release_id != candidate.release_id
        or existing.start_basis != candidate.start_basis
        or existing.duration_days != candidate.duration_days
        or (
            candidate.start_basis != "after_first_access"
            and (_utc(existing.starts_at), _utc(existing.ends_at))
            != (_utc(candidate.starts_at), _utc(candidate.ends_at))
        )
    ):
        raise studytomy_http_error(
            409, "COURSE_GRANT_SOURCE_CONFLICT",
            "This source already retains different course access terms.",
        )
    return existing


def _utc(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


async def resolve_active_course_grants(
    db: AsyncSession,
    *,
    org_id: int,
    learner_profile_id: int,
    course_uuid: str,
    release_id: int,
    now: datetime,
    claim_first_use: bool = False,
    include_unstarted: bool = False,
) -> list[StudyCourseAccessGrant]:
    """Resolve independent grants inside the caller's transaction.

    Claim first use only when serving an already authorized learner read.
    The caller commits the grant clocks with the successful operation.
    """
    if now.utcoffset() is None:
        raise ValueError("Grant evaluation requires a timezone-aware time")
    now = now.astimezone(UTC)
    grant = StudyCourseAccessGrant
    query = (
        select(grant)
        .where(
            grant.org_id == org_id,
            grant.learner_profile_id == learner_profile_id,
            grant.course_uuid == course_uuid,
            grant.release_id == release_id,
            grant.revoked_at.is_(None),
            grant.created_at <= now,
        )
        .order_by(grant.id)
        .execution_options(populate_existing=True)
    )
    if claim_first_use:
        query = query.with_for_update()
    rows = (await db.execute(query)).scalars().all()
    active = []
    for row in rows:
        if row.starts_at is None:
            if row.start_basis != "after_first_access" or row.duration_days is None:
                continue
            if not claim_first_use:
                if include_unstarted:
                    active.append(row)
                continue
            row.starts_at = now
            row.ends_at = now + timedelta(days=row.duration_days)
        if row.ends_at is None:
            continue
        # SQLite fixture timestamps may be returned without their UTC offset.
        start = row.starts_at if row.starts_at.tzinfo else row.starts_at.replace(tzinfo=UTC)
        end = row.ends_at if row.ends_at.tzinfo else row.ends_at.replace(tzinfo=UTC)
        if start <= now < end:
            active.append(row)
    if claim_first_use:
        await db.flush()
    return active
