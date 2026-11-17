"""Learner-controlled sharing, kept separate from guardian and payer authority.

The service only creates explicit, time-bounded grants for an independently
eligible learner. Reading/withdrawing the learner's own choices stays available
so a policy lapse never traps an existing grant. Downstream report delivery must
revalidate with ``has_active_independent_sharing_scope`` at read and send time.
"""

import re
from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.family import (
    StudyEligibilityKind,
    StudyEligibilityOutcome,
    StudyLearnerControlMode,
    StudyLearnerProfile,
    StudyLearnerProfileStatus,
    StudySharingGrant,
    StudySharingGrantCreate,
    StudySharingGrantRead,
    StudySharingGrantStatus,
)
from src.db.studytomy.governance import (
    StudyPolicyBody,
    StudyPolicyKey,
    StudyPolicyVersion,
)
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.services.studytomy.authority import _policy_scope_codes
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.idempotency import claim_idempotency, complete_idempotency
from src.services.studytomy.learners import (
    _current_policies,
    _decision_read,
    _latest_decisions,
)
from src.services.studytomy.policies import _as_utc, lock_organization, lock_users
from src.services.studytomy.privacy import invalidate_protected_studytomy_user_ids

_SHARING_POLICY_KEYS = (
    StudyPolicyKey.D_01,
    StudyPolicyKey.D_03,
    StudyPolicyKey.D_12,
)


def _problem(code: str, message: str, status_code: int = 409) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message}
    )


def _sharing_policy_scope(
    policies: list[StudyPolicyVersion],
) -> tuple[list[int], set[str]]:
    bodies = [StudyPolicyBody.model_validate(policy.body) for policy in policies]
    required_areas = (
        (0, "independent_participation"),
        (1, "adult_transition"),
        (2, "information_uses"),
        (2, "consent"),
        (2, "parent_report_scope"),
    )
    if any(
        bodies[index].specified_value(area) is None for index, area in required_areas
    ):
        raise _problem(
            "POLICY_DECISION_UNRESOLVED",
            "Independent sharing requires explicit D-01, D-03 and D-12 decisions.",
        )
    scopes = _policy_scope_codes(bodies[2].specified_value("parent_report_scope"))
    return [int(policy.id) for policy in policies], scopes


async def _current_independent_context(
    db_session: AsyncSession,
    organization: Organization,
    profile: StudyLearnerProfile,
    now: datetime,
    *,
    scope_code: str | None = None,
) -> list[int]:
    if (
        profile.org_id != organization.id
        or profile.status != StudyLearnerProfileStatus.ACTIVE.value
        or profile.control_mode != StudyLearnerControlMode.INDEPENDENT.value
    ):
        raise _problem(
            "INDEPENDENT_SHARING_UNAVAILABLE",
            "Sharing choices require an active learner with a current independent-participation decision.",
        )
    policies = await _current_policies(
        db_session, organization, _SHARING_POLICY_KEYS, now
    )
    policy_ids, scopes = _sharing_policy_scope(policies)
    if scope_code is not None and scope_code not in scopes:
        raise _problem(
            "SHARING_SCOPE_NOT_APPROVED",
            "The current policy does not approve this sharing scope.",
        )
    decisions = await _latest_decisions(db_session, profile)
    decision = next(
        (
            item
            for item in decisions
            if item.eligibility_kind
            == StudyEligibilityKind.INDEPENDENT_PARTICIPATION.value
        ),
        None,
    )
    if decision is None or decision.outcome != StudyEligibilityOutcome.ELIGIBLE.value:
        raise _problem(
            "INDEPENDENT_SHARING_UNAVAILABLE",
            "Sharing choices require a current human independent-participation decision.",
        )
    read = await _decision_read(db_session, organization, decision, now)
    if not read.currently_eligible or decision.policy_version_ids != policy_ids:
        raise _problem(
            "INDEPENDENT_SHARING_POLICY_STALE",
            "The learner's independent-participation decision must be reviewed under current policies.",
        )
    return policy_ids


async def _member_user(
    db_session: AsyncSession,
    organization: Organization,
    user: User,
) -> bool:
    membership = await db_session.execute(
        select(UserOrganization.id).where(
            UserOrganization.org_id == organization.id,
            UserOrganization.user_id == user.id,
        )
    )
    return membership.scalar_one_or_none() is not None


async def _grant_read(
    db_session: AsyncSession,
    grant: StudySharingGrant,
) -> StudySharingGrantRead:
    recipient = await db_session.get(User, grant.recipient_user_id)
    if recipient is None:
        raise _problem(
            "SHARING_RECIPIENT_UNAVAILABLE", "The selected account is unavailable."
        )
    return StudySharingGrantRead(
        grant_uuid=grant.grant_uuid,
        recipient_user_uuid=recipient.user_uuid,
        scope_codes=list(grant.scope_codes),
        policy_version_ids=list(grant.policy_version_ids),
        status=StudySharingGrantStatus(grant.status),
        valid_from=grant.valid_from,
        valid_to=grant.valid_to,
        authority_version=grant.authority_version,
        withdrawn_at=grant.withdrawn_at,
        created_at=grant.created_at,
    )


async def list_my_sharing_grants(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
) -> list[StudySharingGrantRead]:
    profile_result = await db_session.execute(
        select(StudyLearnerProfile).where(
            StudyLearnerProfile.org_id == organization.id,
            StudyLearnerProfile.user_id == actor.id,
        )
    )
    profile = profile_result.scalars().first()
    if profile is None:
        return []
    result = await db_session.execute(
        select(StudySharingGrant)
        .where(
            StudySharingGrant.org_id == organization.id,
            StudySharingGrant.learner_profile_id == profile.id,
        )
        .order_by(StudySharingGrant.created_at.desc(), StudySharingGrant.id.desc())
    )
    return [await _grant_read(db_session, grant) for grant in result.scalars().all()]


async def create_my_sharing_grant(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    request: StudySharingGrantCreate,
    idempotency_key: str,
) -> StudySharingGrantRead:
    await lock_organization(db_session, int(organization.id))
    profile_result = await db_session.execute(
        select(StudyLearnerProfile)
        .where(
            StudyLearnerProfile.org_id == organization.id,
            StudyLearnerProfile.user_id == actor.id,
        )
        .with_for_update()
    )
    profile = profile_result.scalars().first()
    if profile is None:
        raise HTTPException(
            status_code=404, detail="Learner sharing settings not found"
        )
    learner_user = await db_session.get(User, int(actor.id))
    if learner_user is None or not await _member_user(
        db_session, organization, learner_user
    ):
        raise HTTPException(
            status_code=404, detail="Learner sharing settings not found"
        )
    now = datetime.now(UTC)
    if _as_utc(request.valid_to) <= now:
        raise _problem(
            "INVALID_SHARING_PERIOD", "The sharing end time must be in the future.", 422
        )
    policy_ids = await _current_independent_context(
        db_session, organization, profile, now
    )
    policies = await _current_policies(
        db_session, organization, _SHARING_POLICY_KEYS, now
    )
    _, approved_scopes = _sharing_policy_scope(policies)
    if not set(request.scope_codes).issubset(approved_scopes):
        raise _problem(
            "SHARING_SCOPE_NOT_APPROVED",
            "Sharing scopes must be included in the current D-12 parent-report scope.",
            422,
        )

    recipient_result = await db_session.execute(
        select(User).where(User.user_uuid == request.recipient_user_uuid)
    )
    recipient = recipient_result.scalars().first()
    if (
        recipient is None
        or recipient.id is None
        or int(recipient.id) == int(actor.id)
        or not await _member_user(db_session, organization, recipient)
    ):
        raise HTTPException(status_code=404, detail="Sharing recipient not found")
    await lock_users(db_session, [int(actor.id), int(recipient.id)])

    replay = await claim_idempotency(
        db_session,
        org_id=int(organization.id),
        actor_user_id=int(actor.id),
        operation="studytomy.sharing.create",
        idempotency_key=idempotency_key,
        request_payload={
            "learner_uuid": profile.learner_uuid,
            "grant": request.model_dump(mode="json"),
        },
    )
    if replay is not None:
        if replay.resource_kind != "sharing_grant" or not replay.resource_uuid:
            raise _problem(
                "IDEMPOTENT_SHARING_UNAVAILABLE",
                "The original sharing choice is unavailable.",
            )
        grant_result = await db_session.execute(
            select(StudySharingGrant).where(
                StudySharingGrant.org_id == organization.id,
                StudySharingGrant.grant_uuid == replay.resource_uuid,
                StudySharingGrant.learner_profile_id == profile.id,
            )
        )
        grant = grant_result.scalars().first()
        if grant is None:
            raise _problem(
                "IDEMPOTENT_SHARING_UNAVAILABLE",
                "The original sharing choice is unavailable.",
            )
        return await _grant_read(db_session, grant)

    overlap_result = await db_session.execute(
        select(StudySharingGrant).where(
            StudySharingGrant.org_id == organization.id,
            StudySharingGrant.learner_profile_id == profile.id,
            StudySharingGrant.recipient_user_id == recipient.id,
            StudySharingGrant.status == StudySharingGrantStatus.ACTIVE.value,
            StudySharingGrant.valid_to > now,
            StudySharingGrant.valid_from < request.valid_to,
        )
    )
    for existing in overlap_result.scalars().all():
        if now < _as_utc(existing.valid_to) and set(existing.scope_codes).intersection(
            request.scope_codes
        ):
            raise _problem(
                "OVERLAPPING_SHARING_GRANT",
                "An active choice already covers one or more selected scopes for this recipient.",
            )

    grant = StudySharingGrant(
        org_id=int(organization.id),
        learner_profile_id=int(profile.id),
        recipient_user_id=int(recipient.id),
        created_by_user_id=int(actor.id),
        scope_codes=list(request.scope_codes),
        policy_version_ids=policy_ids,
        status=StudySharingGrantStatus.ACTIVE.value,
        valid_from=now,
        valid_to=request.valid_to,
        authority_version=1,
        created_at=now,
        updated_at=now,
    )
    profile.access_version += 1
    profile.updated_at = now
    db_session.add(grant)
    db_session.add(profile)
    await db_session.flush()
    invalidate_protected_studytomy_user_ids(db_session)
    await append_business_event(
        db_session,
        org_id=int(organization.id),
        actor_user_id=int(actor.id),
        actor_capacity="independent_learner",
        subject_uuid=profile.learner_uuid,
        resource_kind="sharing_grant",
        resource_uuid=grant.grant_uuid,
        action="studytomy.sharing.granted",
        reason_code="VOLUNTARY_SHARING_GRANTED",
        before_state=None,
        after_state={
            "status": grant.status,
            "scope_codes": grant.scope_codes,
            "valid_from": grant.valid_from.isoformat(),
            "valid_to": grant.valid_to.isoformat(),
            "access_version": profile.access_version,
        },
        policy_refs=policy_ids,
        dedupe_key=f"studytomy:sharing:{grant.grant_uuid}:v1",
    )
    await complete_idempotency(
        db_session,
        org_id=int(organization.id),
        actor_user_id=int(actor.id),
        operation="studytomy.sharing.create",
        idempotency_key=idempotency_key,
        resource_kind="sharing_grant",
        resource_uuid=grant.grant_uuid,
        response_status=201,
        response_projection={"grant_uuid": grant.grant_uuid},
    )
    await db_session.commit()
    await db_session.refresh(grant)
    return await _grant_read(db_session, grant)


async def withdraw_my_sharing_grant(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    grant_uuid: str,
    expected_version: int,
    idempotency_key: str,
) -> StudySharingGrantRead:
    await lock_organization(db_session, int(organization.id))
    profile_result = await db_session.execute(
        select(StudyLearnerProfile)
        .where(
            StudyLearnerProfile.org_id == organization.id,
            StudyLearnerProfile.user_id == actor.id,
        )
        .with_for_update()
    )
    profile = profile_result.scalars().first()
    if profile is None:
        raise HTTPException(
            status_code=404, detail="Learner sharing settings not found"
        )
    result = await db_session.execute(
        select(StudySharingGrant)
        .where(
            StudySharingGrant.org_id == organization.id,
            StudySharingGrant.learner_profile_id == profile.id,
            StudySharingGrant.grant_uuid == grant_uuid,
        )
        .with_for_update()
    )
    grant = result.scalars().first()
    if grant is None:
        raise HTTPException(status_code=404, detail="Sharing choice not found")

    replay = await claim_idempotency(
        db_session,
        org_id=int(organization.id),
        actor_user_id=int(actor.id),
        operation="studytomy.sharing.withdraw",
        idempotency_key=idempotency_key,
        request_payload={
            "learner_uuid": profile.learner_uuid,
            "grant_uuid": grant_uuid,
            "expected_version": expected_version,
        },
    )
    if replay is not None:
        if replay.resource_uuid != grant.grant_uuid:
            raise _problem(
                "IDEMPOTENT_SHARING_UNAVAILABLE",
                "The original sharing choice is unavailable.",
            )
        return await _grant_read(db_session, grant)
    if expected_version != grant.authority_version:
        raise _problem(
            "STALE_SHARING_VERSION",
            "The sharing choice changed. Reload it before withdrawing.",
        )
    if grant.status == StudySharingGrantStatus.ACTIVE.value:
        now = datetime.now(UTC)
        before_state = {
            "status": grant.status,
            "scope_codes": list(grant.scope_codes),
            "authority_version": grant.authority_version,
        }
        grant.status = StudySharingGrantStatus.WITHDRAWN.value
        grant.withdrawn_at = now
        grant.authority_version += 1
        grant.updated_at = now
        profile.access_version += 1
        profile.updated_at = now
        db_session.add(grant)
        db_session.add(profile)
        await db_session.flush()
        invalidate_protected_studytomy_user_ids(db_session)
        await append_business_event(
            db_session,
            org_id=int(organization.id),
            actor_user_id=int(actor.id),
            actor_capacity="independent_learner",
            subject_uuid=profile.learner_uuid,
            resource_kind="sharing_grant",
            resource_uuid=grant.grant_uuid,
            action="studytomy.sharing.withdrawn",
            reason_code="VOLUNTARY_SHARING_WITHDRAWN",
            before_state=before_state,
            after_state={
                "status": grant.status,
                "authority_version": grant.authority_version,
                "access_version": profile.access_version,
            },
            policy_refs=list(grant.policy_version_ids),
            dedupe_key=f"studytomy:sharing:{grant.grant_uuid}:v{grant.authority_version}",
        )
    await complete_idempotency(
        db_session,
        org_id=int(organization.id),
        actor_user_id=int(actor.id),
        operation="studytomy.sharing.withdraw",
        idempotency_key=idempotency_key,
        resource_kind="sharing_grant",
        resource_uuid=grant.grant_uuid,
        response_status=200,
        response_projection={"grant_uuid": grant.grant_uuid},
    )
    await db_session.commit()
    await db_session.refresh(grant)
    return await _grant_read(db_session, grant)


async def current_independent_sharing_grant(
    db_session: AsyncSession,
    organization: Organization,
    profile: StudyLearnerProfile,
    recipient_user_id: int,
    scope_code: str,
    *,
    now: datetime | None = None,
) -> StudySharingGrant | None:
    """Resolve exactly one current learner-chosen grant; ambiguity denies access."""
    if (
        not isinstance(scope_code, str)
        or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", scope_code) is None
        or profile.status != StudyLearnerProfileStatus.ACTIVE.value
        or profile.control_mode != StudyLearnerControlMode.INDEPENDENT.value
        or profile.org_id != organization.id
        or int(profile.user_id) == int(recipient_user_id)
    ):
        return None
    effective_now = now or datetime.now(UTC)
    try:
        policy_ids = await _current_independent_context(
            db_session, organization, profile, effective_now, scope_code=scope_code
        )
    except HTTPException:
        return None
    recipient_result = await db_session.execute(
        select(User).where(User.id == recipient_user_id)
    )
    recipient = recipient_result.scalars().first()
    if recipient is None or not await _member_user(db_session, organization, recipient):
        return None
    learner_user = await db_session.get(User, int(profile.user_id))
    if learner_user is None or not await _member_user(
        db_session, organization, learner_user
    ):
        return None
    grants = await db_session.execute(
        select(StudySharingGrant).where(
            StudySharingGrant.org_id == organization.id,
            StudySharingGrant.learner_profile_id == profile.id,
            StudySharingGrant.recipient_user_id == recipient_user_id,
            StudySharingGrant.status == StudySharingGrantStatus.ACTIVE.value,
            StudySharingGrant.valid_from <= effective_now,
            StudySharingGrant.valid_to > effective_now,
        )
    )
    matching = [
        grant
        for grant in grants.scalars().all()
        if grant.policy_version_ids == policy_ids and scope_code in grant.scope_codes
    ]
    return matching[0] if len(matching) == 1 else None


async def has_active_independent_sharing_scope(
    db_session: AsyncSession,
    organization: Organization,
    profile: StudyLearnerProfile,
    recipient_user_id: int,
    scope_code: str,
    *,
    now: datetime | None = None,
) -> bool:
    return (
        await current_independent_sharing_grant(
            db_session, organization, profile, recipient_user_id, scope_code, now=now
        )
        is not None
    )
