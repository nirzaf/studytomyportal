"""Default-private public projections for explicit Studytomy learner profiles.

This module does not infer a learner's age. Until the applicable audience,
publicity and authority policies are approved and a purpose-specific permission
record exists, every account linked to a Studytomy learner profile is treated
as protected in public projections. Accounts without such a profile keep the
existing Studytomy Portal projection unchanged.
"""

import secrets
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.studytomy.family import StudyLearnerProfile
from src.db.users import (
    OrgMemberUserRead,
    PublicUser,
    User,
    UserReadAuthor,
    UserReadPublic,
)
from src.services.studytomy.errors import POLICY_NOT_APPROVED, studytomy_http_error

_PROFILE_USER_IDS_CACHE = "studytomy_protected_profile_user_ids"
_AUTHOR_PSEUDONYM_CACHE = "studytomy_author_pseudonyms"


def _acting_user_id(viewer: Any) -> int:
    # Import lazily because auth imports user services, which use this module
    # for their privacy-aware projections.
    from src.security.auth import resolve_acting_user_id

    return resolve_acting_user_id(viewer)


async def protected_studytomy_user_ids(db_session: AsyncSession) -> frozenset[int]:
    """Return the protected user IDs once per request-scoped DB session."""
    session_info = db_session.sync_session.info
    cached = session_info.get(_PROFILE_USER_IDS_CACHE)
    if cached is not None:
        return cached

    result = await db_session.execute(
        select(StudyLearnerProfile.user_id).distinct()
    )
    protected = frozenset(int(user_id) for user_id in result.scalars().all())
    session_info[_PROFILE_USER_IDS_CACHE] = protected
    return protected


def invalidate_protected_studytomy_user_ids(db_session: AsyncSession) -> None:
    """Invalidate the request cache after a learner profile is created."""
    db_session.sync_session.info.pop(_PROFILE_USER_IDS_CACHE, None)


async def is_protected_studytomy_learner(
    db_session: AsyncSession,
    user_id: int,
) -> bool:
    return user_id in await protected_studytomy_user_ids(db_session)


async def require_studytomy_learner_ai_lookup(
    db_session: AsyncSession,
    user_id: int,
) -> None:
    """Keep protected learner identity out of AI member lookup results.

    Staff authorization for the ordinary roster does not approve sending a
    learner's identity or profile to an AI provider. Until the applicable
    learner AI policy is approved, direct AI lookup fails closed.
    """
    if await is_protected_studytomy_learner(db_session, user_id):
        raise HTTPException(status_code=404, detail="Resource not found")


async def require_studytomy_learner_ai_policy(
    db_session: AsyncSession,
    viewer: Any,
) -> None:
    """Keep unrestricted AI transports separate from managed learner guidance.

    Managed learners use the activity-scoped service that verifies current
    purpose, consent and assessment state. Legacy chat, author tools, RAG and
    MCP do not provide that contract, including when the adaptive UI is off.
    The account-level classification also covers delegated API tokens.
    """
    user_id = _acting_user_id(viewer)
    if user_id <= 0:
        return
    profile_id = (
        await db_session.execute(
            select(StudyLearnerProfile.id)
            .where(StudyLearnerProfile.user_id == user_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if profile_id is None:
        return

    raise studytomy_http_error(
        409,
        POLICY_NOT_APPROVED,
        "AI assistance for this learner is unavailable on this entry point. Use approved lesson guidance.",
    )


def studytomy_work_visibility_filter(author_id_column: Any, viewer: Any) -> Any:
    """SQL predicate for default-private work authored by a learner profile.

    There is no approved publicity grant model yet. Until one exists, work by
    a protected learner is visible only to that same account. Ordinary
    Studytomy Portal authors retain the existing community visibility behavior.
    """
    viewer_id = viewer if isinstance(viewer, int) else _acting_user_id(viewer)
    return or_(
        author_id_column == viewer_id,
        ~author_id_column.in_(select(StudyLearnerProfile.user_id)),
    )


def studytomy_work_ai_visibility_filter(author_id_column: Any) -> Any:
    """Keep protected learner work out of AI tool inputs until D-10 is approved."""
    return ~author_id_column.in_(select(StudyLearnerProfile.user_id))


async def can_read_studytomy_work(
    db_session: AsyncSession,
    viewer: object,
    author_id: int,
) -> bool:
    """Whether the viewer may read a work item under the current safe default."""
    return (
        author_id == _acting_user_id(viewer)
        or not await is_protected_studytomy_learner(db_session, author_id)
    )


async def require_studytomy_work_read(
    db_session: AsyncSession,
    viewer: object,
    author_id: int,
) -> None:
    """Hide protected learner work from non-owners with a generic not-found."""
    if not await can_read_studytomy_work(db_session, viewer, author_id):
        raise HTTPException(status_code=404, detail="Resource not found")


async def require_studytomy_work_ai_processing(
    db_session: AsyncSession,
    author_id: int,
) -> None:
    """Do not send protected learner-authored work to an AI tool/model."""
    if await is_protected_studytomy_learner(db_session, author_id):
        raise HTTPException(status_code=404, detail="Resource not found")


async def can_read_public_profile(
    db_session: AsyncSession,
    viewer: object,
    target: User,
) -> bool:
    """Only the account itself may use legacy profile lookup for a protected learner.

    No generic role, superadmin flag or API-token scope is publicity permission.
    A later approved policy can add a purpose-specific, revocable grant.
    """
    if target.id is None or not await is_protected_studytomy_learner(db_session, target.id):
        return True
    return isinstance(viewer, PublicUser) and viewer.user_uuid == target.user_uuid


async def project_public_user(
    db_session: AsyncSession,
    user: User,
) -> UserReadPublic:
    """Build the existing public user shape, stripping free-form profile data
    for a protected learner in staff-managed rosters.
    """
    projection = UserReadPublic.model_validate(user)
    if user.id is not None and await is_protected_studytomy_learner(db_session, user.id):
        projection.bio = ""
        projection.details = None
        projection.profile = None
    return projection


async def project_org_member_user(
    db_session: AsyncSession,
    user: User,
    allowed_extra_metadata_keys: set[str] | None,
) -> OrgMemberUserRead:
    """Keep generic staff rosters from exposing learner contact/free-form data."""
    projection = OrgMemberUserRead.for_org(user, allowed_extra_metadata_keys)
    if user.id is not None and await is_protected_studytomy_learner(db_session, user.id):
        projection.email = ""
        projection.bio = ""
        projection.details = None
        projection.profile = None
        projection.extra_metadata = None
    return projection


async def project_public_author(
    db_session: AsyncSession,
    user: User,
    *,
    scope_uuid: str,
) -> UserReadAuthor:
    """Project an author chip without exposing a protected learner identity.

    The random pseudonym is scoped to one resource and one response session, so
    separate works or requests cannot be linked to the same learner. It
    contains no real user ID, UUID, name, username or avatar reference. ``id=0``
    keeps existing response shapes while ensuring account popups and profile
    lookups do not target the real account.
    """
    if user.id is None or not await is_protected_studytomy_learner(db_session, user.id):
        return UserReadAuthor.model_validate(user)

    session_info = db_session.sync_session.info
    aliases = session_info.setdefault(_AUTHOR_PSEUDONYM_CACHE, {})
    alias_key = (scope_uuid, int(user.id))
    pseudonym = aliases.get(alias_key)
    if pseudonym is None:
        pseudonym = secrets.token_hex(12)
        aliases[alias_key] = pseudonym
    return UserReadAuthor(
        id=0,
        user_uuid=f"user_private_{pseudonym}",
        username="learner",
        first_name="Learner",
        last_name="",
        avatar_image="",
    )
