"""Staff review authorization for confidential safeguarding case metadata."""

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from fastapi import HTTPException, UploadFile
from sqlalchemy import or_
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError
from typing import Annotated
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.roles import Role, RoleTypeEnum
from src.db.studytomy.cases import StudySafeguardingNarrative, StudyServiceCase, StudyServiceCaseExclusion
from src.db.studytomy.governance import (
    StudyOwnerAssignment, StudyOwnerFunction, StudyPolicyKey, StudyPolicyVersion, StudyStaffScope,
    inspect_study_policy_body,
)
from src.db.studytomy.operations import StudyEvidenceFile
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.security.studytomy_access import (
    StudytomyAccessDecision,
    StudytomyCapabilityName,
    _extract_studytomy_capabilities,
    evaluate_studytomy_staff_access,
)
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.evidence_files import (
    _approved_file_rules, _open_private_evidence, _policy_area_values, _SAFE_RETENTION_CATEGORY, _store_private_evidence,
)
from src.services.studytomy.learners import _active_policy, _current_policies
from src.services.studytomy.idempotency import claim_idempotency, complete_idempotency
from src.services.studytomy.policies import lock_organization, require_org_member_row


class SafeguardingReviewStatus(StrEnum):
    AWAITING_INFORMATION = "awaiting_information"
    UNDER_REVIEW = "under_review"
    RESOLVED = "resolved"
    CLOSED = "closed"


_SAFEGUARDING_TRANSITIONS = {
    "assigned": {SafeguardingReviewStatus.AWAITING_INFORMATION, SafeguardingReviewStatus.UNDER_REVIEW},
    "awaiting_information": {SafeguardingReviewStatus.UNDER_REVIEW},
    "under_review": {SafeguardingReviewStatus.AWAITING_INFORMATION, SafeguardingReviewStatus.RESOLVED},
    "resolved": {SafeguardingReviewStatus.CLOSED},
}


async def require_safeguarding_policies(
    db_session: AsyncSession, organization: Organization,
) -> list[int]:
    now = datetime.now(UTC)
    keys = (StudyPolicyKey.D_04, StudyPolicyKey.D_11, StudyPolicyKey.D_12, StudyPolicyKey.D_13)
    return [int((await _active_policy(db_session, organization, key, now)).id) for key in keys]


async def _require_case_review(
    db_session: AsyncSession,
    organization: Organization,
    current_user: PublicUser,
    case_uuid: UUID,
    *,
    for_update: bool = False,
) -> tuple[StudyServiceCase, StudytomyAccessDecision]:
    """Resolve only an in-tenant safeguarding case and enforce independent staff review."""
    statement = select(StudyServiceCase).where(
        StudyServiceCase.org_id == organization.id,
        StudyServiceCase.case_uuid == str(case_uuid),
        StudyServiceCase.category == "safeguarding",
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    case = (await db_session.exec(statement)).one_or_none()
    if case is None:
        raise HTTPException(404, "Case not found.")

    exclusion = await db_session.get(
        StudyServiceCaseExclusion, (organization.id, case.case_uuid, current_user.id)
    )
    decision = await evaluate_studytomy_staff_access(
        db_session,
        current_user,
        organization,
        StudytomyCapabilityName.SAFEGUARDING_CASE_REVIEW,
        target_resource_uuid=case.case_uuid,
        subject_user_id=case.reporter_user_id,
        excluded_user_ids=(exclusion.user_id,) if exclusion is not None else (),
        access_version=case.version,
    )
    if not decision.allowed:
        raise HTTPException(404, "Case not found.")
    try:
        policy_ids = await require_safeguarding_policies(db_session, organization)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        raise HTTPException(404, "Case not found.") from None
    if decision.policy_version_ids != policy_ids:
        raise HTTPException(404, "Case not found.")
    return case, decision


async def require_safeguarding_case_review(
    db_session: AsyncSession,
    organization: Organization,
    current_user: PublicUser,
    case_uuid: UUID,
) -> StudyServiceCase:
    case, _ = await _require_case_review(db_session, organization, current_user, case_uuid)
    return case


async def assign_safeguarding_case(
    db_session: AsyncSession,
    organization: Organization,
    current_user: PublicUser,
    case_uuid: UUID,
    *,
    expected_version: int,
    next_owner_uuid: str | None = None,
) -> StudyServiceCase:
    """Claim or transfer a case and retain its assignment event in one transaction."""
    await lock_organization(db_session, organization.id)
    case, decision = await _require_case_review(
        db_session, organization, current_user, case_uuid, for_update=True,
    )
    if case.version != expected_version:
        raise HTTPException(409, "The case has changed. Reload before assigning it.")
    if case.status in {"resolved", "closed"}:
        raise HTTPException(409, "Only open cases may be assigned.")
    if case.owner_user_id not in (None, current_user.id):
        raise HTTPException(403, "Only the current owner can transfer this case.")
    next_owner = current_user
    if next_owner_uuid is not None and next_owner_uuid != current_user.user_uuid:
        candidate = (await db_session.exec(
            select(User).where(User.user_uuid == next_owner_uuid).limit(2)
        )).all()
        if len(candidate) != 1:
            raise HTTPException(404, "Case not found.")
        next_owner = PublicUser.model_validate(candidate[0])
        await _require_case_review(db_session, organization, next_owner, case_uuid)
    if case.owner_user_id == next_owner.id:
        return case

    before = {"status": case.status, "case_owner_user_id": case.owner_user_id}
    case.owner_user_id = next_owner.id
    if case.status == "acknowledged":
        case.status = "assigned"
    case.version += 1
    await append_business_event(
        db_session,
        org_id=organization.id,
        actor_user_id=current_user.id,
        actor_capacity="safeguarding_case_reviewer",
        resource_kind="case",
        resource_uuid=case.case_uuid,
        action="studytomy.safeguarding.assign_case",
        reason_code="SAFEGUARDING_CASE_ASSIGNMENT",
        before_state=before,
        after_state={"status": case.status, "case_owner_user_id": case.owner_user_id},
        policy_refs=decision.policy_version_ids,
    )
    return case


async def transition_safeguarding_case(
    db_session: AsyncSession,
    organization: Organization,
    current_user: PublicUser,
    case_uuid: UUID,
    *,
    expected_version: int,
    next_status: SafeguardingReviewStatus,
) -> StudyServiceCase:
    """Record a review step by the current independent owner."""
    await lock_organization(db_session, organization.id)
    case, decision = await _require_case_review(
        db_session, organization, current_user, case_uuid, for_update=True,
    )
    if case.owner_user_id != current_user.id:
        raise HTTPException(403, "Only the current owner can advance this case.")
    if case.version != expected_version:
        raise HTTPException(409, "The case has changed. Reload before updating it.")
    if next_status not in _SAFEGUARDING_TRANSITIONS.get(case.status, ()):
        raise HTTPException(409, "The requested case transition is unavailable.")

    before = {"status": case.status, "version": case.version}
    case.status = next_status.value
    case.version += 1
    await append_business_event(
        db_session,
        org_id=organization.id,
        actor_user_id=current_user.id,
        actor_capacity="safeguarding_case_reviewer",
        resource_kind="case",
        resource_uuid=case.case_uuid,
        action="studytomy.safeguarding.transition_case",
        reason_code="SAFEGUARDING_CASE_TRANSITION",
        before_state=before,
        after_state={"status": case.status, "version": case.version},
        policy_refs=decision.policy_version_ids,
    )
    return case


class SafeguardingStaffing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead_owner_assignment_uuid: UUID
    backup_owner_assignment_uuid: UUID


def approved_safeguarding_staffing(policy: StudyPolicyVersion) -> SafeguardingStaffing | None:
    body, blockers = inspect_study_policy_body("D-04", policy.body)
    if body is None or any(blockers.values()):
        return None
    staffing = body.specified_value("staffing")
    if not isinstance(staffing, dict):
        return None
    try:
        roles = SafeguardingStaffing.model_validate(staffing.get("safeguarding"))
    except ValidationError:
        return None
    if roles.lead_owner_assignment_uuid == roles.backup_owner_assignment_uuid:
        return None
    return roles


async def _active_safeguarding_staffing(
    db_session: AsyncSession, organization: Organization, policy_ids: list[int],
) -> tuple[StudyPolicyVersion, list[tuple[StudyOwnerAssignment, User]]] | None:
    policy = (await db_session.exec(select(StudyPolicyVersion).where(
        StudyPolicyVersion.id.in_(policy_ids),
        StudyPolicyVersion.org_id == organization.id,
        StudyPolicyVersion.policy_key == "D-04",
        StudyPolicyVersion.status == "approved",
    ))).one_or_none()
    roles = approved_safeguarding_staffing(policy) if policy is not None else None
    if roles is None or policy is None:
        return None
    now = datetime.now(UTC)
    candidates = []
    for reference in (roles.lead_owner_assignment_uuid, roles.backup_owner_assignment_uuid):
        rows = (await db_session.exec(
            select(StudyOwnerAssignment, User, Role)
            .join(User, User.id == StudyOwnerAssignment.user_id)
            .join(UserOrganization, UserOrganization.user_id == User.id)
            .join(Role, Role.id == UserOrganization.role_id)
            .where(
                StudyOwnerAssignment.org_id == organization.id,
                StudyOwnerAssignment.owner_uuid == str(reference),
                StudyOwnerAssignment.scope_kind == "organization",
                StudyOwnerAssignment.scope_uuid == organization.org_uuid,
                StudyOwnerAssignment.responsibility == StudyOwnerFunction.SAFEGUARDING.value,
                StudyOwnerAssignment.status == "active",
                StudyOwnerAssignment.effective_from <= now,
                or_(StudyOwnerAssignment.effective_to.is_(None), StudyOwnerAssignment.effective_to > now),
                UserOrganization.org_id == organization.id,
                Role.org_id == organization.id,
                Role.role_type == RoleTypeEnum.TYPE_ORGANIZATION,
            )
            .limit(2)
        )).all()
        if len(rows) != 1:
            return None
        assignment, user, role = rows[0]
        if not _extract_studytomy_capabilities(role).safeguarding_case_review:
            return None
        candidates.append((assignment, user))
    if candidates[0][1].id == candidates[1][1].id:
        return None
    return policy, candidates


def _online_reporting_enabled() -> bool:
    return os.getenv("LEARNHOUSE_STUDYTOMY_SAFEGUARDING_REPORTS_ENABLED") == "true"


async def safeguarding_reporting_available(
    db_session: AsyncSession, organization: Organization,
) -> bool:
    if not _online_reporting_enabled():
        return False
    try:
        policy_ids = await require_safeguarding_policies(db_session, organization)
        await public_safeguarding_contact(db_session, organization)
        await _require_narrative_retention(db_session, organization, policy_ids)
    except HTTPException as exc:
        if exc.status_code in (404, 409):
            return False
        raise
    return await _active_safeguarding_staffing(db_session, organization, policy_ids) is not None


async def _route_new_safeguarding_case(
    db_session: AsyncSession,
    organization: Organization,
    case: StudyServiceCase,
    policy_ids: list[int],
    excluded_ids: set[int],
) -> None:
    staffing = await _active_safeguarding_staffing(db_session, organization, policy_ids)
    if staffing is None:
        return
    policy, candidates = staffing
    capability = StudytomyCapabilityName.SAFEGUARDING_CASE_REVIEW
    for assignment, user in candidates:
        if user.id == case.reporter_user_id or user.id in excluded_ids:
            continue
        # Retain the exact new-case grant only if the normal policy and ACL evaluator accepts it.
        async with db_session.begin_nested() as trial:
            db_session.add(StudyStaffScope(
                org_id=organization.id, assignment_id=assignment.id,
                capability=capability.value, scope_kind="case", scope_uuid=case.case_uuid,
                evidence_reference=f"approved-policy:{policy.policy_uuid}",
                created_by_user_id=policy.approved_by_user_id or policy.created_by_user_id,
            ))
            await db_session.flush()
            decision = await evaluate_studytomy_staff_access(
                db_session, PublicUser.model_validate(user), organization, capability, target_resource_uuid=case.case_uuid,
                subject_user_id=case.reporter_user_id, excluded_user_ids=excluded_ids,
            )
            if not decision.allowed or decision.policy_version_ids != policy_ids:
                await trial.rollback()
                continue
        await append_business_event(
            db_session, org_id=organization.id, actor_user_id=None, actor_machine_id="studytomy",
            resource_kind="case", resource_uuid=case.case_uuid,
            action="studytomy.safeguarding.route_case", reason_code="SAFEGUARDING_ROUTED",
            after_state={"case_owner_user_id": user.id, "status": "assigned"}, policy_refs=policy_ids,
        )
        case.owner_user_id = user.id
        case.status = "assigned"
        return


async def _require_narrative_retention(
    db_session: AsyncSession, organization: Organization, policy_ids: list[int],
) -> tuple[int, str]:
    policy = (await _current_policies(
        db_session, organization, (StudyPolicyKey.D_13,), datetime.now(UTC),
    ))[0]
    retention = _policy_area_values(policy, "retention").get("safeguarding_evidence_retention_category")
    if policy.id not in policy_ids or not isinstance(retention, str) or not _SAFE_RETENTION_CATEGORY.fullmatch(retention):
        raise HTTPException(409, "Approved safeguarding evidence retention is required.")
    return int(policy.id), retention


async def resolve_case_subjects(
    db_session: AsyncSession, organization: Organization, subjects: list[str],
) -> set[int]:
    matches = (await db_session.exec(
        select(User.id, User.user_uuid)
        .join(UserOrganization, UserOrganization.user_id == User.id)
        .where(UserOrganization.org_id == organization.id, User.user_uuid.in_(subjects))
    )).all()
    if len(matches) != len(subjects) or {match.user_uuid for match in matches} != set(subjects):
        raise HTTPException(422, "One or more case subjects could not be identified in this organization.")
    return {match.id for match in matches}


async def open_safeguarding_case(
    db_session: AsyncSession,
    organization: Organization,
    reporter: PublicUser,
    *,
    request_id: UUID,
    subject_user_uuids: list[str],
    narrative: str | None = None,
) -> dict:
    """Create a confidential acknowledgement and exclusions without lesson access."""
    await lock_organization(db_session, organization.id)
    await require_org_member_row(db_session, organization.id, reporter.id)
    policy_ids = await require_safeguarding_policies(db_session, organization)
    subjects = sorted(set(subject_user_uuids))
    payload: dict = {"subject_user_uuids": subjects}
    if narrative is not None:
        narrative = narrative.strip()
        if not 1 <= len(narrative) <= 8000 or "\x00" in narrative:
            raise HTTPException(422, "Enter between 1 and 8000 characters of plain text.")
        payload["narrative"] = narrative
    operation = "studytomy.safeguarding.open_case"
    replay = await claim_idempotency(
        db_session, org_id=organization.id, actor_user_id=reporter.id,
        operation=operation, idempotency_key=str(request_id),
        request_payload=payload,
    )
    if replay is not None:
        return replay.response_projection

    if narrative is not None and not _online_reporting_enabled():
        raise HTTPException(409, "Online safeguarding reporting is unavailable.")
    subject_ids = await resolve_case_subjects(db_session, organization, subjects)
    if narrative is not None:
        await public_safeguarding_contact(db_session, organization)
        if await _active_safeguarding_staffing(db_session, organization, policy_ids) is None:
            raise HTTPException(409, detail={
                "code": "SAFEGUARDING_STAFFING_UNAVAILABLE",
                "message": "Online safeguarding reporting is unavailable.",
            })
        retention_policy_id, retention = await _require_narrative_retention(db_session, organization, policy_ids)

    case = StudyServiceCase(
        org_id=organization.id, reporter_user_id=reporter.id, category="safeguarding",
    )
    db_session.add(case)
    await db_session.flush()
    if narrative is not None:
        db_session.add(StudySafeguardingNarrative(
            org_id=organization.id, case_uuid=case.case_uuid, narrative=narrative,
            retention_policy_id=retention_policy_id, retention_category=retention,
        ))
    db_session.add_all([
        StudyServiceCaseExclusion(org_id=organization.id, case_uuid=case.case_uuid, user_id=subject_id)
        for subject_id in subject_ids
    ])
    await append_business_event(
        db_session, org_id=organization.id, actor_user_id=reporter.id,
        resource_kind="case", resource_uuid=case.case_uuid, action=operation,
        reason_code="SAFEGUARDING_CASE_ACKNOWLEDGED", after_state={"status": case.status},
        policy_refs=policy_ids,
    )
    # Unidentified people mentioned in a narrative need independent triage before routing.
    if narrative is None or subject_ids:
        await _route_new_safeguarding_case(
            db_session, organization, case, policy_ids, subject_ids,
        )
    receipt = {"case_uuid": case.case_uuid, "status": case.status, "version": case.version}
    await complete_idempotency(
        db_session, org_id=organization.id, actor_user_id=reporter.id,
        operation=operation, idempotency_key=str(request_id),
        resource_kind="case", resource_uuid=case.case_uuid,
        response_status=201, response_projection=receipt,
    )
    return receipt


async def list_unassigned_safeguarding_cases(
    db_session: AsyncSession,
    organization: Organization,
    reviewer: PublicUser,
) -> list[StudyServiceCase]:
    """Return only unassigned cases already covered by the reviewer's explicit scope."""
    await require_org_member_row(db_session, organization.id, reviewer.id)
    await require_safeguarding_policies(db_session, organization)
    now = datetime.now(UTC)
    capability = StudytomyCapabilityName.SAFEGUARDING_CASE_REVIEW.value
    has_case_grant = (
        select(StudyStaffScope.id)
        .join(StudyOwnerAssignment, StudyOwnerAssignment.id == StudyStaffScope.assignment_id)
        .where(
            StudyStaffScope.org_id == organization.id,
            StudyStaffScope.scope_kind == "case",
            StudyStaffScope.scope_uuid == StudyServiceCase.case_uuid,
            StudyStaffScope.capability.in_((capability, f"studytomy.{capability}")),
            StudyStaffScope.status == "active",
            StudyStaffScope.is_exclusion.is_(False),
            StudyOwnerAssignment.org_id == organization.id,
            StudyOwnerAssignment.user_id == reviewer.id,
            StudyOwnerAssignment.responsibility == StudyOwnerFunction.SAFEGUARDING.value,
            StudyOwnerAssignment.scope_kind == "organization",
            StudyOwnerAssignment.scope_uuid == organization.org_uuid,
            StudyOwnerAssignment.status == "active",
            StudyOwnerAssignment.effective_from <= now,
            or_(StudyOwnerAssignment.effective_to.is_(None), StudyOwnerAssignment.effective_to > now),
        )
        .exists()
    )
    is_excluded = select(StudyServiceCaseExclusion.user_id).where(
        StudyServiceCaseExclusion.org_id == organization.id,
        StudyServiceCaseExclusion.case_uuid == StudyServiceCase.case_uuid,
        StudyServiceCaseExclusion.user_id == reviewer.id,
    ).exists()
    candidates = (await db_session.exec(
        select(StudyServiceCase.case_uuid)
        .where(
            StudyServiceCase.org_id == organization.id,
            StudyServiceCase.category == "safeguarding",
            StudyServiceCase.status == "acknowledged",
            StudyServiceCase.reporter_user_id != reviewer.id,
            has_case_grant,
            ~is_excluded,
        )
        .order_by(StudyServiceCase.id)
        .limit(100)
    )).all()
    visible = []
    for reference in candidates:
        try:
            case = await require_safeguarding_case_review(db_session, organization, reviewer, UUID(reference))
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            continue
        if case.status == "acknowledged":
            visible.append(case)
    return visible


async def open_safeguarding_case_evidence(
    db_session: AsyncSession,
    organization: Organization,
    reviewer: PublicUser,
    case_uuid: UUID,
    evidence_uuid: str,
) -> tuple[str, AsyncIterator[bytes]]:
    """Read ready evidence under the current independent case ACL and policy."""
    case, decision = await _require_case_review(db_session, organization, reviewer, case_uuid)
    evidence = (await db_session.exec(select(StudyEvidenceFile).where(
        StudyEvidenceFile.org_id == organization.id,
        StudyEvidenceFile.safeguarding_case_uuid == case.case_uuid,
        StudyEvidenceFile.evidence_uuid == evidence_uuid,
        StudyEvidenceFile.purpose == "safeguarding_case_evidence",
        StudyEvidenceFile.upload_status == "ready",
    ))).one_or_none()
    if evidence is None:
        raise HTTPException(404, "Evidence file not found.")
    d15 = await _current_policies(db_session, organization, (StudyPolicyKey.D_15,), datetime.now(UTC))
    if evidence.policy_version_ids != [*decision.policy_version_ids, int(d15[0].id)]:
        raise HTTPException(409, detail={
            "code": "EVIDENCE_POLICY_STALE",
            "message": "The evidence is unavailable pending review under the current policy.",
        })
    return evidence.media_type, await _open_private_evidence(organization, evidence)


async def upload_safeguarding_case_evidence(
    db_session: AsyncSession,
    organization: Organization,
    reviewer: PublicUser,
    case_uuid: UUID,
    file: UploadFile,
    idempotency_key: str,
) -> str:
    """Retain an attachment after verifying the current case owner's authority."""
    await lock_organization(db_session, organization.id)
    case, decision = await _require_case_review(
        db_session, organization, reviewer, case_uuid, for_update=True,
    )
    if case.owner_user_id != reviewer.id:
        raise HTTPException(403, "Only the current owner can attach evidence.")
    if case.status in {"resolved", "closed"}:
        raise HTTPException(409, "Evidence can only be added to an open case.")
    now = datetime.now(UTC)
    policies = await _current_policies(
        db_session, organization, (StudyPolicyKey.D_13, StudyPolicyKey.D_15), now,
    )
    file_rules = _approved_file_rules(policies, retention_field="safeguarding_evidence_retention_category")
    record = await _store_private_evidence(
        db_session, organization, reviewer, file, idempotency_key,
        purpose="safeguarding_case_evidence",
        policy_ids=[*decision.policy_version_ids, int(policies[1].id)],
        file_rules=file_rules, now=now, safeguarding_case_uuid=case.case_uuid,
    )
    return record.evidence_uuid


async def read_safeguarding_narrative(
    db_session: AsyncSession, organization: Organization, reviewer: PublicUser, case_uuid: UUID,
) -> str:
    case, decision = await _require_case_review(db_session, organization, reviewer, case_uuid)
    record = await db_session.get(StudySafeguardingNarrative, (organization.id, case.case_uuid))
    if record is None:
        raise HTTPException(404, "Report narrative not found.")
    if record.retention_policy_id not in decision.policy_version_ids:
        raise HTTPException(409, "The report narrative requires review under the current retention policy.")
    return record.narrative


_PublicGuidanceText = Annotated[str, StringConstraints(
    strict=True, strip_whitespace=True, min_length=1, max_length=1000,
)]


class PublicSafeguardingContact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_instructions: _PublicGuidanceText
    monitored_hours: _PublicGuidanceText
    emergency_guidance: _PublicGuidanceText


async def public_safeguarding_contact(
    db_session: AsyncSession, organization: Organization,
) -> PublicSafeguardingContact:
    """Read only the fields explicitly approved for public safeguarding guidance."""
    try:
        policy = await _active_policy(db_session, organization, StudyPolicyKey.D_04, datetime.now(UTC))
        routes = _policy_area_values(policy, "support_routes")
        hours = _policy_area_values(policy, "monitored_hours")
        contact = routes.get("public_safeguarding")
        if not isinstance(contact, dict):
            raise HTTPException(404, "Public safeguarding contact is unavailable.")
        return PublicSafeguardingContact.model_validate({
            "contact_instructions": contact.get("contact_instructions"),
            "emergency_guidance": contact.get("emergency_guidance"),
            "monitored_hours": hours.get("public_safeguarding"),
        })
    except ValidationError:
        raise HTTPException(404, "Public safeguarding contact is unavailable.") from None
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        raise HTTPException(404, "Public safeguarding contact is unavailable.") from None


async def get_own_safeguarding_report_status(
    db_session: AsyncSession, organization: Organization, reporter: PublicUser, case_uuid: UUID,
) -> str:
    """Expose the status of the reporter's own case without reading confidential evidence."""
    status = (await db_session.exec(
        select(StudyServiceCase.status)
        .join(
            UserOrganization,
            (UserOrganization.org_id == StudyServiceCase.org_id)
            & (UserOrganization.user_id == StudyServiceCase.reporter_user_id),
        )
        .where(
            StudyServiceCase.org_id == organization.id,
            StudyServiceCase.case_uuid == str(case_uuid),
            StudyServiceCase.category == "safeguarding",
            StudyServiceCase.reporter_user_id == reporter.id,
        )
    )).one_or_none()
    if status is None:
        raise HTTPException(404, "Report not found.")
    return status


async def _require_safeguarding_triage(
    db_session: AsyncSession, organization: Organization, reviewer: PublicUser,
) -> list[int]:
    policy_ids = await require_safeguarding_policies(db_session, organization)
    staffing = await _active_safeguarding_staffing(db_session, organization, policy_ids)
    if staffing is None or reviewer.id not in {user.id for _, user in staffing[1]}:
        raise HTTPException(404, "Triage queue not found.")
    return policy_ids


def _safeguarding_triage_predicates(organization: Organization, reviewer: PublicUser):
    excluded_participant = select(StudyServiceCaseExclusion.user_id).where(
        StudyServiceCaseExclusion.org_id == organization.id,
        StudyServiceCaseExclusion.case_uuid == StudyServiceCase.case_uuid,
        StudyServiceCaseExclusion.user_id == reviewer.id,
    ).exists()
    now = datetime.now(UTC)
    capability = StudytomyCapabilityName.SAFEGUARDING_CASE_REVIEW.value
    excluded_scope = (
        select(StudyStaffScope.id)
        .join(StudyOwnerAssignment, StudyOwnerAssignment.id == StudyStaffScope.assignment_id)
        .where(
            StudyStaffScope.org_id == organization.id,
            StudyStaffScope.capability.in_((capability, f"studytomy.{capability}")),
            StudyStaffScope.status == "active",
            StudyStaffScope.is_exclusion.is_(True),
            StudyOwnerAssignment.org_id == organization.id,
            StudyOwnerAssignment.user_id == reviewer.id,
            StudyOwnerAssignment.responsibility == StudyOwnerFunction.SAFEGUARDING.value,
            StudyOwnerAssignment.scope_kind == "organization",
            StudyOwnerAssignment.scope_uuid == organization.org_uuid,
            StudyOwnerAssignment.status == "active",
            StudyOwnerAssignment.effective_from <= now,
            or_(StudyOwnerAssignment.effective_to.is_(None), StudyOwnerAssignment.effective_to > now),
            or_(
                (StudyStaffScope.scope_kind == "case") & (StudyStaffScope.scope_uuid == StudyServiceCase.case_uuid),
                (StudyStaffScope.scope_kind == "organization") & StudyStaffScope.scope_uuid.is_(None),
            ),
        ).exists()
    )
    return (
        StudyServiceCase.org_id == organization.id,
        StudyServiceCase.category == "safeguarding",
        StudyServiceCase.status == "acknowledged",
        StudyServiceCase.reporter_user_id != reviewer.id,
        ~excluded_participant,
        ~excluded_scope,
    )


async def list_safeguarding_triage(
    db_session: AsyncSession,
    organization: Organization,
    reviewer: PublicUser,
    *,
    after: UUID | None = None,
) -> list[tuple[str, int]]:
    """Let named safeguarding staff discover unassigned cases without granting case access."""
    await _require_safeguarding_triage(db_session, organization, reviewer)
    statement = select(StudyServiceCase.case_uuid, StudyServiceCase.version).where(
        *_safeguarding_triage_predicates(organization, reviewer),
    )
    if after is not None:
        statement = statement.where(StudyServiceCase.case_uuid > str(after))
    return list((await db_session.exec(statement.order_by(StudyServiceCase.case_uuid).limit(100))).tuples())


async def record_safeguarding_triage(
    db_session: AsyncSession,
    organization: Organization,
    reviewer: PublicUser,
    case_uuid: UUID,
    *,
    expected_version: int,
    verification_reference: UUID,
    subject_user_uuids: list[str],
) -> StudyServiceCase:
    """Record the staff attestation and route to a reviewer independent of the triager."""
    await lock_organization(db_session, organization.id)
    policy_ids = await _require_safeguarding_triage(db_session, organization, reviewer)
    case = (await db_session.exec(
        select(StudyServiceCase)
        .where(
            *_safeguarding_triage_predicates(organization, reviewer),
            StudyServiceCase.case_uuid == str(case_uuid),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )).one_or_none()
    if case is None:
        raise HTTPException(404, "Triage case not found.")
    if case.version != expected_version:
        raise HTTPException(409, "The case has changed. Reload before recording triage.")
    existing = set((await db_session.exec(select(StudyServiceCaseExclusion.user_id).where(
        StudyServiceCaseExclusion.org_id == organization.id,
        StudyServiceCaseExclusion.case_uuid == case.case_uuid,
    ))).all())
    confirmed = await resolve_case_subjects(db_session, organization, sorted(set(subject_user_uuids)))
    if reviewer.id in confirmed:
        raise HTTPException(403, "Another approved reviewer must record this triage.")
    db_session.add_all([
        StudyServiceCaseExclusion(org_id=organization.id, case_uuid=case.case_uuid, user_id=subject)
        for subject in confirmed - existing
    ])
    case.version += 1
    await append_business_event(
        db_session, org_id=organization.id, actor_user_id=reviewer.id,
        actor_capacity="safeguarding_triage", resource_kind="case", resource_uuid=case.case_uuid,
        action="studytomy.safeguarding.record_triage", reason_code="SAFEGUARDING_TRIAGE_RECORDED",
        before_state={"version": expected_version},
        after_state={"version": case.version, "evidence_reference": str(verification_reference)},
        policy_refs=policy_ids,
    )
    await _route_new_safeguarding_case(
        db_session, organization, case, policy_ids, existing | confirmed | {reviewer.id},
    )
    return case
