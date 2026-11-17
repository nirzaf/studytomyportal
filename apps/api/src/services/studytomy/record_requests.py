"""Learner-owned intake for reviewed access to retained learning records."""

from datetime import UTC, datetime, timedelta
from re import fullmatch
from typing import Literal
from uuid import UUID

from fastapi import UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import aliased
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyOwnerAssignment,
    StudyOwnerFunction,
    StudyOwnerStatus,
    StudyPolicyKey,
    StudyPolicyVersion,
    StudyStaffScope,
    inspect_study_policy_body,
)
from src.db.studytomy.operations import StudyEvidenceFile
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser
from src.security.session_context import POLICY_AUTH_METHODS, get_session_provenance
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_studytomy_staff_action,
    evaluate_studytomy_staff_access,
)
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.evidence_files import (
    _approved_file_rules,
    _verify_private_evidence,
    store_private_learner_evidence,
)
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization

_ACTION = "studytomy.record_request.submitted"
_DECISION = "studytomy.record_request.decided"
DecisionOutcome = Literal["approved_for_preparation", "denied"]
_OUTCOMES = ("approved_for_preparation", "denied")
_REASON_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


class RecordRequestReceipt(BaseModel):
    request_reference: UUID
    received_at: datetime
    due_at: datetime


class RecordRequestStatus(RecordRequestReceipt):
    status: Literal["received", "approved_for_preparation", "denied"]
    decided_at: datetime | None = None


class RecordRequestDecisionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: DecisionOutcome
    reason_code: str = Field(pattern=_REASON_PATTERN)
    evidence_reference: str = Field(pattern=r"^ev_[A-Za-z0-9_-]{16,80}$")


class RecordRequestDecisionReceipt(BaseModel):
    request_reference: UUID
    outcome: DecisionOutcome
    decided_at: datetime


class RecordRequestEvidenceReceipt(BaseModel):
    evidence_reference: str
    content_sha256: str
    verified_at: datetime


async def check_my_learning_record_request(db: AsyncSession, org: Organization, actor: PublicUser) -> None:
    await _self_profile(db, org, actor)
    await _intake_policy(db, org, datetime.now(UTC))


async def _self_profile(db: AsyncSession, org: Organization, actor: PublicUser) -> StudyLearnerProfile:
    _require_direct_session()
    profile = (await db.execute(select(StudyLearnerProfile).where(
        StudyLearnerProfile.org_id == org.id, StudyLearnerProfile.user_id == actor.id,
        StudyLearnerProfile.control_mode == "independent",
    ))).scalar_one_or_none()
    if profile is None:
        raise studytomy_http_error(404, "RECORD_REQUEST_UNAVAILABLE", "Record request unavailable.")
    return profile


def _record_access_rule(retention: StudyPolicyVersion) -> dict:
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_13, retention.body)
    access = body.specified_value("record_access") if body else None
    rule = access.get("self_learning_record_request") if isinstance(access, dict) else None
    return rule if isinstance(rule, dict) else {}


def _record_request_scopes(information: StudyPolicyVersion) -> list[str]:
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_12, information.body)
    uses = body.specified_value("information_uses") if body else None
    scopes = uses.get("record_request") if isinstance(uses, dict) else None
    return scopes if isinstance(scopes, list) else []


async def _intake_policy(db: AsyncSession, org: Organization, now: datetime):
    policies = await _current_policies(db, org, (StudyPolicyKey.D_03, StudyPolicyKey.D_12, StudyPolicyKey.D_13), now)
    _, information, retention = policies
    rule = _record_access_rule(retention)
    days = rule.get("due_days")
    owner_uuid = rule.get("privacy_owner_assignment_uuid")
    if (
        "self_learning_records" not in _record_request_scopes(information)
        or type(days) is not int or not 1 <= days <= 365
        or not isinstance(owner_uuid, str) or not owner_uuid
    ):
        raise studytomy_http_error(409, "RECORD_REQUEST_NOT_APPROVED", "The current policy does not approve record request intake.")
    owner = StudyOwnerAssignment
    assignment_id = (await db.execute(select(owner.id).where(
        owner.org_id == org.id, owner.owner_uuid == owner_uuid,
        owner.responsibility == StudyOwnerFunction.PRIVACY.value,
        owner.scope_kind == "organization", owner.scope_uuid == org.org_uuid,
        owner.status == StudyOwnerStatus.ACTIVE.value, owner.effective_from <= now,
        (owner.effective_to.is_(None) | (owner.effective_to > now)),
        select(UserOrganization.id).where(
            UserOrganization.org_id == org.id, UserOrganization.user_id == owner.user_id,
        ).exists(),
    ))).scalar_one_or_none()
    if assignment_id is None:
        raise studytomy_http_error(409, "RECORD_REQUEST_NOT_APPROVED", "The current policy does not approve record request intake.")
    return policies, days, assignment_id


async def submit_my_learning_record_request(
    db: AsyncSession, org: Organization, actor: PublicUser, request_reference: UUID,
) -> RecordRequestReceipt:
    org = await lock_organization(db, org.id)
    profile = await _self_profile(db, org, actor)
    key = f"studytomy:record_request:{org.id}:{request_reference}"
    retained = (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id,
        StudyBusinessEvent.action == _ACTION, StudyBusinessEvent.dedupe_key == key,
    ))).scalar_one_or_none()
    if retained is not None:
        if retained.actor_user_id != actor.id or retained.subject_uuid != profile.learner_uuid:
            raise studytomy_http_error(404, "RECORD_REQUEST_UNAVAILABLE", "Record request unavailable.")
        return _receipt(retained)
    now = datetime.now(UTC)
    policies, days, assignment_id = await _intake_policy(db, org, now)
    event = await append_business_event(
        db, org_id=org.id, actor_user_id=actor.id, action=_ACTION, reason_code="SELF_LEARNING_RECORDS_REQUESTED",
        subject_uuid=profile.learner_uuid, resource_kind="record_request", resource_uuid=str(request_reference),
        dedupe_key=key, policy_refs=[policy.id for policy in policies],
        after_state={"status": "received", "scope_codes": ["self_learning_records"],
                     "review_due_at": (now + timedelta(days=days)).isoformat(), "owner_assignment_id": assignment_id},
    )
    return _receipt(event)


async def read_my_learning_record_request_status(
    db: AsyncSession, org: Organization, actor: PublicUser, request_reference: UUID,
) -> RecordRequestStatus:
    profile = await _self_profile(db, org, actor)
    event = (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.actor_user_id == actor.id,
        StudyBusinessEvent.subject_uuid == profile.learner_uuid, StudyBusinessEvent.action == _ACTION,
        StudyBusinessEvent.dedupe_key == f"studytomy:record_request:{org.id}:{request_reference}",
    ))).scalar_one_or_none()
    if event is None:
        raise studytomy_http_error(404, "RECORD_REQUEST_UNAVAILABLE", "Record request unavailable.")
    _, information, retention = await _current_policies(
        db, org, (StudyPolicyKey.D_03, StudyPolicyKey.D_12, StudyPolicyKey.D_13), datetime.now(UTC),
    )
    if (
        "self_learning_request_status" not in _record_request_scopes(information)
        or _record_access_rule(retention).get("learner_status_enabled") is not True
    ):
        raise studytomy_http_error(409, "RECORD_REQUEST_STATUS_NOT_APPROVED", "Current policy does not approve request status.")
    decision = (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.subject_uuid == profile.learner_uuid,
        StudyBusinessEvent.resource_uuid == str(request_reference), StudyBusinessEvent.operation_uuid == event.event_uuid,
        StudyBusinessEvent.action == _DECISION,
        StudyBusinessEvent.dedupe_key == f"studytomy:record_request:decision:{org.id}:{request_reference}",
    ))).scalar_one_or_none()
    return RecordRequestStatus(
        **_receipt(event).model_dump(),
        status=(decision.after_state or {}).get("status") if decision else "received",
        decided_at=decision.created_at if decision else None,
    )


async def read_learning_record_request_for_review(
    db: AsyncSession, org: Organization, actor: PublicUser, request_reference: UUID,
) -> RecordRequestReceipt:
    event, _, _ = await _staff_request_context(db, org, actor, request_reference)
    return _receipt(event)


async def list_pending_learning_record_requests(
    db: AsyncSession, org: Organization, actor: PublicUser, *, limit: int,
) -> list[RecordRequestReceipt]:
    _require_direct_session()
    now = datetime.now(UTC)
    _, _, assignment_id = await _intake_policy(db, org, now)
    owner = await db.get(StudyOwnerAssignment, assignment_id)
    if owner is None or owner.user_id != actor.id:
        raise studytomy_http_error(404, "RECORD_REQUEST_UNAVAILABLE", "Record request unavailable.")
    event = StudyBusinessEvent
    decision = aliased(StudyBusinessEvent)
    scope = StudyStaffScope
    capability = StudytomyCapabilityName.PRIVACY_RETENTION_REVIEW
    grants = select(scope.id).where(
        scope.org_id == org.id, scope.assignment_id == assignment_id, scope.status == "active",
        scope.capability.in_((capability.value, f"studytomy.{capability.value}")),
        scope.scope_kind == "case", scope.scope_uuid == event.resource_uuid, scope.is_exclusion.is_(False),
    ).exists()
    decided = select(decision.id).where(
        decision.org_id == org.id, decision.action == _DECISION, decision.operation_uuid == event.event_uuid,
    ).exists()
    cases = (await db.execute(select(event).where(
        event.org_id == org.id, event.actor_user_id != actor.id, event.action == _ACTION,
        event.after_state["owner_assignment_id"].as_integer() == assignment_id,
        grants, ~decided,
    ).order_by(event.id).limit(limit))).scalars().all()
    results = []
    for case in cases:
        access = await evaluate_studytomy_staff_access(
            db, actor, org, capability, target_scope_kind="case", target_resource_uuid=case.resource_uuid, now=now,
        )
        if access.allowed:
            results.append(_receipt(case))
    return results


async def _staff_request_context(
    db: AsyncSession, org: Organization, actor: PublicUser, request_reference: UUID,
):
    _require_direct_session()
    now = datetime.now(UTC)
    authority = await authorize_studytomy_staff_action(
        db, actor, org, StudytomyCapabilityName.PRIVACY_RETENTION_REVIEW,
        target_scope_kind="case", target_resource_uuid=str(request_reference),
        hide_existence_on_denial=True, now=now,
    )
    policies, _, assignment_id = await _intake_policy(db, org, now)
    owner = await db.get(StudyOwnerAssignment, assignment_id)
    event = (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id,
        StudyBusinessEvent.action == _ACTION,
        StudyBusinessEvent.dedupe_key == f"studytomy:record_request:{org.id}:{request_reference}",
    ))).scalar_one_or_none()
    if (
        event is None or owner is None or owner.user_id != actor.id or event.actor_user_id == actor.id
        or (event.after_state or {}).get("owner_assignment_id") != assignment_id
    ):
        raise studytomy_http_error(404, "RECORD_REQUEST_UNAVAILABLE", "Record request unavailable.")
    return event, policies, authority.policy_version_ids


async def decide_learning_record_request(
    db: AsyncSession, org: Organization, actor: PublicUser, request_reference: UUID,
    command: RecordRequestDecisionCommand,
) -> RecordRequestDecisionReceipt:
    org = await lock_organization(db, org.id)
    request, policies, authority_refs = await _staff_request_context(db, org, actor, request_reference)
    if command.reason_code not in _decision_reasons(policies[2]).get(command.outcome, []):
        raise studytomy_http_error(409, "RECORD_REQUEST_DECISION_NOT_APPROVED", "Current policy does not approve this decision.")
    key = f"studytomy:record_request:decision:{org.id}:{request_reference}"
    digest = canonical_request_hash(command)
    existing = await _retained_decision(db, org.id, key)
    if existing is not None:
        if (existing.after_state or {}).get("source_snapshot_digest") != digest:
            raise studytomy_http_error(409, "RECORD_REQUEST_ALREADY_DECIDED", "This request already has a decision.")
        event = existing
    else:
        evidence_policies, _ = await _case_evidence_policy(db, org, policies, datetime.now(UTC))
        evidence = (await db.execute(select(StudyEvidenceFile).where(
            StudyEvidenceFile.org_id == org.id, StudyEvidenceFile.record_request_event_uuid == request.event_uuid,
            StudyEvidenceFile.purpose == "record_request_evidence",
            StudyEvidenceFile.evidence_uuid == command.evidence_reference,
            StudyEvidenceFile.upload_status == "ready", StudyEvidenceFile.verified_at.is_not(None),
        ))).scalar_one_or_none()
        if evidence is None or evidence.policy_version_ids != [policy.id for policy in evidence_policies]:
            raise studytomy_http_error(409, "RECORD_REQUEST_EVIDENCE_UNAVAILABLE", "Verified evidence for this request is required.")
        await _verify_private_evidence(org, evidence)
        event = await append_business_event(
            db, org_id=org.id, actor_user_id=actor.id, action=_DECISION, reason_code="RECORD_REQUEST_REVIEWED",
            subject_uuid=request.subject_uuid, resource_kind="record_request", resource_uuid=str(request_reference),
            operation_uuid=request.event_uuid, dedupe_key=key,
            policy_refs=sorted({policy.id for policy in policies} | set(authority_refs)),
            after_state={"status": command.outcome, "decision_code": command.reason_code,
                         "evidence_reference": command.evidence_reference,
                         "evidence_sha256": evidence.content_sha256, "source_snapshot_digest": digest},
        )
    return RecordRequestDecisionReceipt(
        request_reference=request_reference, outcome=command.outcome, decided_at=event.created_at,
    )


async def _case_evidence_policy(
    db: AsyncSession, org: Organization, policies: list[StudyPolicyVersion], now: datetime,
) -> tuple[list[StudyPolicyVersion], tuple[int, set[str], str]]:
    _, information, retention = policies
    if (
        "staff_review_evidence" not in _record_request_scopes(information)
        or _record_access_rule(retention).get("evidence_upload_enabled") is not True
    ):
        raise studytomy_http_error(409, "RECORD_REQUEST_EVIDENCE_NOT_APPROVED", "Case evidence is unavailable under current policy.")
    accessibility, = await _current_policies(db, org, (StudyPolicyKey.D_15,), now)
    evidence_policies = [*policies, accessibility]
    return evidence_policies, _approved_file_rules(evidence_policies, retention_field="record_request_evidence_retention_category")


async def upload_record_request_evidence(
    db: AsyncSession, org: Organization, actor: PublicUser, request_reference: UUID,
    file: UploadFile, idempotency_key: str,
) -> RecordRequestEvidenceReceipt:
    org = await lock_organization(db, org.id)
    request, policies, _ = await _staff_request_context(db, org, actor, request_reference)
    if await _retained_decision(db, org.id, f"studytomy:record_request:decision:{org.id}:{request_reference}"):
        raise studytomy_http_error(409, "RECORD_REQUEST_ALREADY_DECIDED", "This request already has a decision.")
    now = datetime.now(UTC)
    evidence_policies, rules = await _case_evidence_policy(db, org, policies, now)
    profile = (await db.execute(select(StudyLearnerProfile).where(
        StudyLearnerProfile.org_id == org.id, StudyLearnerProfile.learner_uuid == request.subject_uuid,
        StudyLearnerProfile.user_id == request.actor_user_id,
    ))).scalar_one_or_none()
    if profile is None:
        raise studytomy_http_error(404, "RECORD_REQUEST_UNAVAILABLE", "Record request unavailable.")
    evidence = await store_private_learner_evidence(
        db, org, actor, profile, file, idempotency_key, policies=evidence_policies, file_rules=rules,
        now=now, record_request_event_uuid=request.event_uuid,
    )
    return RecordRequestEvidenceReceipt(
        evidence_reference=evidence.evidence_uuid, content_sha256=evidence.content_sha256, verified_at=evidence.verified_at,
    )


def _decision_reasons(retention: StudyPolicyVersion) -> dict[DecisionOutcome, list[str]]:
    configured = _record_access_rule(retention).get("decision_reason_codes")
    if not isinstance(configured, dict):
        return {}
    options = {}
    for outcome in _OUTCOMES:
        reasons = configured.get(outcome)
        if isinstance(reasons, list):
            valid = list(dict.fromkeys(
                reason for reason in reasons if isinstance(reason, str) and fullmatch(_REASON_PATTERN, reason)
            ))
            if valid:
                options[outcome] = valid
    return options


async def _retained_decision(db: AsyncSession, org_id: int, key: str) -> StudyBusinessEvent | None:
    return (await db.execute(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org_id, StudyBusinessEvent.action == _DECISION,
        StudyBusinessEvent.dedupe_key == key,
    ))).scalar_one_or_none()


async def read_record_request_decision_options(
    db: AsyncSession, org: Organization, actor: PublicUser, request_reference: UUID,
) -> dict[DecisionOutcome, list[str]]:
    _, policies, _ = await _staff_request_context(db, org, actor, request_reference)
    retained = await _retained_decision(db, org.id, f"studytomy:record_request:decision:{org.id}:{request_reference}")
    if retained is not None:
        return {}
    await _case_evidence_policy(db, org, policies, datetime.now(UTC))
    return _decision_reasons(policies[2])


def _require_direct_session() -> None:
    provenance = get_session_provenance()
    if provenance is None or provenance.amr not in POLICY_AUTH_METHODS:
        raise studytomy_http_error(403, "INTERACTIVE_SESSION_REQUIRED", "Sign in directly to continue.")


def _receipt(event: StudyBusinessEvent) -> RecordRequestReceipt:
    return RecordRequestReceipt(
        request_reference=event.resource_uuid, received_at=event.created_at,
        due_at=datetime.fromisoformat(event.after_state["review_due_at"]),
    )
