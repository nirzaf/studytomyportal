"""Policy-bound, self-service consent history for Studytomy learner accounts.

Consent is kept as append-only evidence. New choices require current approved
policy, the exact notice version, and nonblank display text configured per
purpose in D-12. Until D-01, D-03 and the purpose-specific decisions are
approved, writes fail closed. Guardian and dependent-learner writes are
deliberately unavailable until their authority is explicitly defined and
implemented.
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
)
from src.db.studytomy.governance import (
    StudyPolicyBody,
    StudyPolicyKey,
    StudyPolicyVersion,
)
from src.db.studytomy.privacy import (
    StudyConsentChoice,
    StudyConsentEvent,
    StudyConsentEventCreate,
    StudyConsentEventRead,
    StudyConsentNoticeRead,
    StudyConsentPurpose,
)
from src.db.users import PublicUser
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.idempotency import claim_idempotency, complete_idempotency
from src.services.studytomy.learners import (
    _current_policies,
    _decision_read,
    _latest_decisions,
)
from src.services.studytomy.policies import _as_utc, lock_organization

_BASE_POLICY_KEYS = (StudyPolicyKey.D_01, StudyPolicyKey.D_03, StudyPolicyKey.D_12)
_PURPOSE_POLICY_KEYS: dict[StudyConsentPurpose, tuple[StudyPolicyKey, ...]] = {
    StudyConsentPurpose.LEARNING_TERMS: (StudyPolicyKey.D_05, StudyPolicyKey.D_18),
    StudyConsentPurpose.AI_ASSISTANCE: (StudyPolicyKey.D_10,),
    StudyConsentPurpose.COMMUNITY: (StudyPolicyKey.D_11,),
    StudyConsentPurpose.SHARING: (StudyPolicyKey.D_13,),
    StudyConsentPurpose.MARKETING: (StudyPolicyKey.D_14,),
    StudyConsentPurpose.PUBLICITY: (StudyPolicyKey.D_11, StudyPolicyKey.D_16, StudyPolicyKey.D_18),
}
_REQUIRED_SPECIFIED_AREAS: dict[StudyPolicyKey, tuple[str, ...]] = {
    StudyPolicyKey.D_01: ("independent_participation",),
    StudyPolicyKey.D_03: ("adult_transition",),
    StudyPolicyKey.D_05: ("prices", "access_periods", "learner_allocation"),
    StudyPolicyKey.D_10: (
        "learner_eligibility",
        "supervision",
        "materials",
        "usage_limits",
        "review",
    ),
    StudyPolicyKey.D_11: ("community_audiences", "adult_contact_boundaries", "moderation"),
    StudyPolicyKey.D_13: ("retention", "record_access", "correction", "removal"),
    StudyPolicyKey.D_14: ("contacts", "marketing_choices"),
    StudyPolicyKey.D_16: ("content_rights", "review_approvals"),
    StudyPolicyKey.D_18: ("customer_terms", "selected_market_requirements"),
}
_SCOPE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def _problem(code: str, message: str, status_code: int = 409) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _policy_map(policies: list[StudyPolicyVersion]) -> dict[StudyPolicyKey, StudyPolicyVersion]:
    mapped: dict[StudyPolicyKey, StudyPolicyVersion] = {}
    for policy in policies:
        try:
            key = StudyPolicyKey(policy.policy_key)
        except ValueError:
            continue
        mapped[key] = policy
    return mapped


def _body_value(
    policy: StudyPolicyVersion,
    policy_key: StudyPolicyKey,
    area: str,
) -> object:
    body = StudyPolicyBody.model_validate(policy.body)
    value = body.specified_value(area)
    if value is None:
        raise _problem(
            "CONSENT_POLICY_INCOMPLETE",
            f"The approved {policy_key.value} policy does not specify {area} for this consent purpose.",
        )
    return value


def _approved_scopes_and_notice(
    policy_map: dict[StudyPolicyKey, StudyPolicyVersion],
    purpose: StudyConsentPurpose,
) -> tuple[set[str], str, str]:
    d12 = policy_map.get(StudyPolicyKey.D_12)
    if d12 is None:
        raise _problem("POLICY_NOT_APPROVED", "A current D-12 information-use policy is required.")

    information_uses = _body_value(d12, StudyPolicyKey.D_12, "information_uses")
    if not isinstance(information_uses, dict):
        raise _problem(
            "CONSENT_POLICY_INVALID",
            "D-12 information_uses must map each approved purpose to its scope codes.",
        )
    raw_scopes = information_uses.get(purpose.value)
    if (
        not isinstance(raw_scopes, list)
        or not raw_scopes
        or len(raw_scopes) > 64
        or any(not isinstance(code, str) or _SCOPE_CODE.fullmatch(code) is None for code in raw_scopes)
        or len(set(raw_scopes)) != len(raw_scopes)
    ):
        raise _problem(
            "CONSENT_PURPOSE_NOT_APPROVED",
            "This purpose has no valid scope in the current approved D-12 policy.",
        )

    consent_policy = _body_value(d12, StudyPolicyKey.D_12, "consent")
    notice_map = consent_policy.get("notice_versions") if isinstance(consent_policy, dict) else None
    notice_version = notice_map.get(purpose.value) if isinstance(notice_map, dict) else None
    if (
        not isinstance(notice_version, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}", notice_version) is None
    ):
        raise _problem(
            "CONSENT_NOTICE_UNAPPROVED",
            "The current D-12 policy has no exact approved notice version for this purpose.",
        )
    # A version reference alone cannot support an informed choice. Require the
    # display text in the same immutable, approved D-12 decision; never accept
    # caller-supplied copy as a substitute.
    notice_texts = consent_policy.get("notice_texts") if isinstance(consent_policy, dict) else None
    notice_text = notice_texts.get(purpose.value) if isinstance(notice_texts, dict) else None
    if not isinstance(notice_text, str) or not notice_text.strip():
        raise _problem(
            "CONSENT_NOTICE_UNAVAILABLE",
            "The current D-12 policy has no displayable approved notice for this purpose.",
        )
    return set(raw_scopes), notice_version, notice_text.strip()


async def _require_current_independent_learner(
    db_session: AsyncSession,
    organization: Organization,
    profile: StudyLearnerProfile,
    policies: list[StudyPolicyVersion],
    now: datetime,
) -> None:
    if (
        profile.org_id != organization.id
        or profile.status != StudyLearnerProfileStatus.ACTIVE.value
        or profile.control_mode != StudyLearnerControlMode.INDEPENDENT.value
    ):
        raise _problem(
            "CONSENT_ACTOR_NOT_AUTHORIZED",
            "Consent choices are available only to an active, independently authorized learner acting for themselves.",
            403,
        )

    policy_map = _policy_map(policies)
    for key, areas in _REQUIRED_SPECIFIED_AREAS.items():
        if key not in _BASE_POLICY_KEYS:
            continue
        policy = policy_map.get(key)
        if policy is None:
            raise _problem("POLICY_NOT_APPROVED", f"A current {key.value} policy is required.")
        for area in areas:
            _body_value(policy, key, area)

    base_policy_ids = [int(policy_map[key].id) for key in _BASE_POLICY_KEYS]
    decisions = await _latest_decisions(db_session, profile)
    eligibility = next(
        (
            decision
            for decision in decisions
            if decision.eligibility_kind == StudyEligibilityKind.INDEPENDENT_PARTICIPATION.value
        ),
        None,
    )
    if eligibility is None or eligibility.outcome != StudyEligibilityOutcome.ELIGIBLE.value:
        raise _problem(
            "CONSENT_ACTOR_NOT_AUTHORIZED",
            "A current human independent-participation decision is required for this learner.",
            403,
        )
    eligibility_read = await _decision_read(db_session, organization, eligibility, now)
    if not eligibility_read.currently_eligible or eligibility.policy_version_ids != base_policy_ids:
        raise _problem(
            "CONSENT_POLICY_STALE",
            "The learner's independent-participation decision must be reviewed under current policy.",
        )


async def _consent_context(
    db_session: AsyncSession,
    organization: Organization,
    profile: StudyLearnerProfile,
    purpose: StudyConsentPurpose,
    now: datetime,
) -> tuple[list[StudyPolicyVersion], set[str], str, str]:
    keys = (*_BASE_POLICY_KEYS, *_PURPOSE_POLICY_KEYS[purpose])
    policies = await _current_policies(db_session, organization, keys, now)
    policy_map = _policy_map(policies)
    for key in keys:
        policy = policy_map.get(key)
        if policy is None or policy.id is None:
            raise _problem("POLICY_NOT_APPROVED", f"A current {key.value} policy is required.")
        for area in _REQUIRED_SPECIFIED_AREAS.get(key, ()):
            _body_value(policy, key, area)
    await _require_current_independent_learner(
        db_session, organization, profile, policies, now
    )
    scopes, notice_version, notice_text = _approved_scopes_and_notice(policy_map, purpose)
    return policies, scopes, notice_version, notice_text


def _event_read(event: StudyConsentEvent) -> StudyConsentEventRead:
    return StudyConsentEventRead(
        event_uuid=event.event_uuid,
        purpose=StudyConsentPurpose(event.purpose_code),
        choice=StudyConsentChoice(event.choice),
        withdraws_event_uuid=event.withdraws_event_uuid,
        notice_version=event.notice_version,
        scope_codes=list(event.scope_codes),
        policy_version_ids=list(event.policy_version_ids),
        provenance=event.provenance,
        effective_at=event.effective_at,
        created_at=event.created_at,
    )


async def _owned_profile(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    learner_uuid: str,
    *,
    lock: bool = False,
) -> StudyLearnerProfile:
    statement = select(StudyLearnerProfile).where(
        StudyLearnerProfile.org_id == organization.id,
        StudyLearnerProfile.learner_uuid == learner_uuid,
        StudyLearnerProfile.user_id == actor.id,
    )
    if lock:
        statement = statement.with_for_update()
    result = await db_session.execute(statement)
    profile = result.scalars().first()
    if profile is None:
        raise HTTPException(status_code=404, detail="Learner consent settings not found")
    return profile


async def _my_profile(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
) -> StudyLearnerProfile:
    if actor.id is None:
        raise HTTPException(status_code=401, detail="An authenticated account is required")
    result = await db_session.execute(
        select(StudyLearnerProfile).where(
            StudyLearnerProfile.org_id == organization.id,
            StudyLearnerProfile.user_id == actor.id,
        )
    )
    profile = result.scalars().first()
    if profile is None:
        raise HTTPException(status_code=404, detail="Learner consent settings not found")
    return profile


async def _list_profile_consent_events(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    profile: StudyLearnerProfile,
    limit: int,
) -> list[StudyConsentEventRead]:
    result = await db_session.execute(
        select(StudyConsentEvent)
        .where(
            StudyConsentEvent.org_id == organization.id,
            StudyConsentEvent.learner_profile_id == profile.id,
            StudyConsentEvent.subject_user_id == actor.id,
            StudyConsentEvent.actor_user_id == actor.id,
        )
        .order_by(StudyConsentEvent.effective_at.desc(), StudyConsentEvent.id.desc())
        .limit(max(1, min(limit, 100)))
    )
    return [_event_read(event) for event in result.scalars().all()]


async def list_my_consent_events(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    learner_uuid: str,
    *,
    limit: int = 100,
) -> list[StudyConsentEventRead]:
    profile = await _owned_profile(db_session, organization, actor, learner_uuid)
    return await _list_profile_consent_events(
        db_session, organization, actor, profile, limit
    )


async def list_my_consent_events_for_self(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    *,
    limit: int = 100,
) -> list[StudyConsentEventRead]:
    profile = await _my_profile(db_session, organization, actor)
    return await _list_profile_consent_events(
        db_session, organization, actor, profile, limit
    )


async def get_my_consent_notice(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    purpose: StudyConsentPurpose,
) -> StudyConsentNoticeRead:
    """Return only the current, approved notice for the learner's own profile."""
    profile = await _my_profile(db_session, organization, actor)
    purpose = StudyConsentPurpose(purpose)
    policies, scopes, notice_version, notice_text = await _consent_context(
        db_session,
        organization,
        profile,
        purpose,
        datetime.now(UTC),
    )
    return StudyConsentNoticeRead(
        purpose=purpose,
        notice_version=notice_version,
        notice_text=notice_text,
        scope_codes=sorted(scopes),
        policy_version_ids=[int(policy.id) for policy in policies],
    )


async def record_my_consent_choice(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    learner_uuid: str,
    request: StudyConsentEventCreate,
    idempotency_key: str,
) -> StudyConsentEventRead:
    if actor.id is None:
        raise HTTPException(status_code=401, detail="An authenticated account is required")
    await lock_organization(db_session, int(organization.id))
    profile = await _owned_profile(
        db_session, organization, actor, learner_uuid, lock=True
    )

    purpose = StudyConsentPurpose(request.purpose)
    now = datetime.now(UTC)
    policies, allowed_scopes, notice_version, _notice_text = await _consent_context(
        db_session, organization, profile, purpose, now
    )
    if request.notice_version != notice_version:
        raise _problem(
            "CONSENT_NOTICE_STALE",
            "The submitted choice does not match the current approved notice version.",
            409,
        )
    if not set(request.scope_codes).issubset(allowed_scopes):
        raise _problem(
            "CONSENT_SCOPE_NOT_APPROVED",
            "The selected consent scope is not included in current approved policy.",
            422,
        )

    operation = "studytomy.consent.record"
    replay = await claim_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor.id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_payload=request,
    )
    if replay is not None:
        result = await db_session.execute(
            select(StudyConsentEvent).where(
                StudyConsentEvent.org_id == organization.id,
                StudyConsentEvent.event_uuid == replay.resource_uuid,
                StudyConsentEvent.actor_user_id == actor.id,
            )
        )
        event = result.scalars().first()
        if event is None:
            raise _problem("IDEMPOTENCY_RESULT_MISSING", "The recorded consent result is unavailable.")
        return _event_read(event)

    if profile.id is None or profile.user_id != actor.id:
        raise HTTPException(status_code=404, detail="Learner consent settings not found")
    event = StudyConsentEvent(
        org_id=organization.id,
        learner_profile_id=profile.id,
        subject_user_id=profile.user_id,
        actor_user_id=actor.id,
        purpose_code=purpose.value,
        choice=request.choice.value,
        notice_version=notice_version,
        scope_codes=list(request.scope_codes),
        policy_version_ids=[int(policy.id) for policy in policies],
        provenance="authenticated_self_service",
        effective_at=now,
        created_at=now,
    )
    db_session.add(event)
    await db_session.flush()
    await append_business_event(
        db_session,
        org_id=organization.id,
        actor_user_id=actor.id,
        actor_capacity="learner_self",
        subject_uuid=profile.learner_uuid,
        resource_kind="consent_event",
        resource_uuid=event.event_uuid,
        action="studytomy.consent.recorded",
        reason_code="CONSENT_CHOICE_RECORDED",
        after_state={"status": "accepted" if request.choice == StudyConsentChoice.ACCEPT else "declined"},
        policy_refs=[int(policy.id) for policy in policies],
    )
    await complete_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor.id,
        operation=operation,
        idempotency_key=idempotency_key,
        resource_kind="consent_event",
        resource_uuid=event.event_uuid,
        response_status=201,
    )
    await db_session.commit()
    return _event_read(event)


async def record_my_consent_choice_for_self(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    request: StudyConsentEventCreate,
    idempotency_key: str,
) -> StudyConsentEventRead:
    """Record a choice for the authenticated learner without accepting a subject ID."""
    profile = await _my_profile(db_session, organization, actor)
    return await record_my_consent_choice(
        db_session,
        organization,
        actor,
        profile.learner_uuid,
        request,
        idempotency_key,
    )


async def withdraw_my_consent_choice(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    learner_uuid: str,
    event_uuid: str,
    idempotency_key: str,
) -> StudyConsentEventRead:
    """Append a withdrawal without requiring a still-current notice or policy."""
    if actor.id is None:
        raise HTTPException(status_code=401, detail="An authenticated account is required")
    await lock_organization(db_session, int(organization.id))
    profile = await _owned_profile(
        db_session, organization, actor, learner_uuid, lock=True
    )
    replay = await claim_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor.id,
        operation="studytomy.consent.withdraw",
        idempotency_key=idempotency_key,
        request_payload={"event_uuid": event_uuid},
    )
    if replay is not None:
        result = await db_session.execute(
            select(StudyConsentEvent).where(
                StudyConsentEvent.org_id == organization.id,
                StudyConsentEvent.event_uuid == replay.resource_uuid,
                StudyConsentEvent.actor_user_id == actor.id,
            )
        )
        event = result.scalars().first()
        if event is None:
            raise _problem("IDEMPOTENCY_RESULT_MISSING", "The withdrawal result is unavailable.")
        return _event_read(event)

    source_result = await db_session.execute(
        select(StudyConsentEvent).where(
            StudyConsentEvent.org_id == organization.id,
            StudyConsentEvent.learner_profile_id == profile.id,
            StudyConsentEvent.subject_user_id == actor.id,
            StudyConsentEvent.actor_user_id == actor.id,
            StudyConsentEvent.event_uuid == event_uuid,
            StudyConsentEvent.choice == StudyConsentChoice.ACCEPT.value,
        )
    )
    source = source_result.scalars().first()
    if source is None:
        raise HTTPException(status_code=404, detail="Consent choice not found")

    history_result = await db_session.execute(
        select(StudyConsentEvent)
        .where(
            StudyConsentEvent.org_id == organization.id,
            StudyConsentEvent.learner_profile_id == profile.id,
            StudyConsentEvent.subject_user_id == actor.id,
            StudyConsentEvent.actor_user_id == actor.id,
            StudyConsentEvent.purpose_code == source.purpose_code,
            StudyConsentEvent.effective_at >= source.effective_at,
        )
        .order_by(StudyConsentEvent.effective_at.desc(), StudyConsentEvent.id.desc())
    )
    latest_by_scope: dict[str, StudyConsentEvent] = {}
    for item in history_result.scalars().all():
        for scope in item.scope_codes:
            latest_by_scope.setdefault(scope, item)
    if any(latest_by_scope.get(scope) is not source for scope in source.scope_codes):
        raise _problem(
            "CONSENT_CHOICE_SUPERSEDED",
            "This choice is no longer current; refresh the consent history before withdrawing.",
        )

    now = datetime.now(UTC)
    event = StudyConsentEvent(
        org_id=organization.id,
        learner_profile_id=int(profile.id),
        subject_user_id=actor.id,
        actor_user_id=actor.id,
        purpose_code=source.purpose_code,
        choice=StudyConsentChoice.WITHDRAW.value,
        withdraws_event_uuid=source.event_uuid,
        notice_version=source.notice_version,
        scope_codes=list(source.scope_codes),
        policy_version_ids=list(source.policy_version_ids),
        provenance="authenticated_self_service",
        effective_at=now,
        created_at=now,
    )
    db_session.add(event)
    await db_session.flush()
    await append_business_event(
        db_session,
        org_id=organization.id,
        actor_user_id=actor.id,
        actor_capacity="learner_self",
        subject_uuid=profile.learner_uuid,
        resource_kind="consent_event",
        resource_uuid=event.event_uuid,
        action="studytomy.consent.withdrawn",
        reason_code="CONSENT_CHOICE_WITHDRAWN",
        before_state={"status": "accepted"},
        after_state={"status": "withdrawn"},
        policy_refs=list(source.policy_version_ids),
    )
    await complete_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor.id,
        operation="studytomy.consent.withdraw",
        idempotency_key=idempotency_key,
        resource_kind="consent_event",
        resource_uuid=event.event_uuid,
        response_status=201,
    )
    await db_session.commit()
    return _event_read(event)


async def withdraw_my_consent_choice_for_self(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    event_uuid: str,
    idempotency_key: str,
) -> StudyConsentEventRead:
    """Append a withdrawal for the authenticated learner without a subject ID."""
    profile = await _my_profile(db_session, organization, actor)
    return await withdraw_my_consent_choice(
        db_session,
        organization,
        actor,
        profile.learner_uuid,
        event_uuid,
        idempotency_key,
    )


async def has_current_consent_scope(
    db_session: AsyncSession,
    organization: Organization,
    profile: StudyLearnerProfile,
    purpose: StudyConsentPurpose,
    scope_code: str,
    now: datetime | None = None,
) -> bool:
    """Resolve a current self-consent scope for an independent learner."""
    if (
        _SCOPE_CODE.fullmatch(scope_code) is None
        or profile.org_id != organization.id
        or profile.status != StudyLearnerProfileStatus.ACTIVE.value
        or profile.control_mode != StudyLearnerControlMode.INDEPENDENT.value
    ):
        return False
    effective_now = now or datetime.now(UTC)
    try:
        policies, allowed_scopes, notice_version, _notice_text = await _consent_context(
            db_session, organization, profile, purpose, effective_now
        )
    except HTTPException:
        return False
    if scope_code not in allowed_scopes:
        return False
    current_policy_ids = [int(policy.id) for policy in policies]
    result = await db_session.execute(
        select(StudyConsentEvent)
        .where(
            StudyConsentEvent.org_id == organization.id,
            StudyConsentEvent.learner_profile_id == profile.id,
            StudyConsentEvent.subject_user_id == profile.user_id,
            StudyConsentEvent.actor_user_id == profile.user_id,
            StudyConsentEvent.purpose_code == purpose.value,
        )
        .order_by(StudyConsentEvent.effective_at.desc(), StudyConsentEvent.id.desc())
    )
    for event in result.scalars().all():
        if scope_code not in event.scope_codes:
            continue
        return (
            event.choice == StudyConsentChoice.ACCEPT.value
            and event.notice_version == notice_version
            and event.policy_version_ids == current_policy_ids
            and _as_utc(event.effective_at) <= _as_utc(effective_now)
        )
    return False
