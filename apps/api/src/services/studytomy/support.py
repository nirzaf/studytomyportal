"""Public support guidance projected from approved organization policies."""

import os
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter, ValidationError
from sqlalchemy import or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.cases import StudyServiceCase, StudyServiceCaseExclusion
from src.db.studytomy.governance import StudyOwnerAssignment, StudyOwnerFunction, StudyPolicyKey, StudyPolicyVersion
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.cases import resolve_case_subjects
from src.services.studytomy.idempotency import claim_idempotency, complete_idempotency
from src.services.studytomy.policies import lock_organization, require_org_member_row
from src.services.studytomy.evidence_files import _policy_area_values
from src.services.studytomy.learners import _current_policies

_Guidance = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=1000)]
_Category = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)]
_PublicResponseExpectation = TypeAdapter(_Guidance)


class PublicSupportGuidance(BaseModel):
    categories: Annotated[list[_Category], Field(min_length=1, max_length=12)]
    contact_instructions: _Guidance
    monitored_hours: _Guidance
    service_limits: _Guidance
    response_expectations: _Guidance | None
    intake_available: bool
    guest_verification_available: bool = False


def _support_intake_enabled() -> bool:
    return os.getenv("LEARNHOUSE_STUDYTOMY_SUPPORT_INTAKE_ENABLED") == "true"


async def _guest_verification_available(
    db: AsyncSession, org: Organization, service: StudyPolicyVersion, now: datetime,
) -> bool:
    if os.getenv("LEARNHOUSE_STUDYTOMY_GUEST_SUPPORT_ENABLED") != "true":
        return False
    contact = _policy_area_values(service, "support_routes").get("public_support")
    if (
        not isinstance(contact, dict) or contact.get("guest_intake_enabled") is not True
        or contact.get("guest_verification_delivery") != "email"
    ):
        return False
    try:
        privacy, = await _current_policies(db, org, (StudyPolicyKey.D_12,), now)
        purposes = _policy_area_values(privacy, "information_uses")
    except HTTPException as error:
        if error.status_code != 409:
            raise
        return False
    return purposes.get("guest_support_verification") == ["requester_submitted_email"]


class SupportStaffing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lead_owner_assignment_uuid: UUID
    backup_owner_assignment_uuid: UUID


async def _support_staff(
    db: AsyncSession, org: Organization, policy: StudyPolicyVersion, now: datetime,
) -> tuple[int, ...]:
    staffing = _policy_area_values(policy, "staffing")
    try:
        config = SupportStaffing.model_validate(staffing.get("support"))
    except ValidationError:
        return ()
    lead, backup = str(config.lead_owner_assignment_uuid), str(config.backup_owner_assignment_uuid)
    if lead == backup:
        return ()
    rows = (await db.exec(
        select(StudyOwnerAssignment.owner_uuid, StudyOwnerAssignment.user_id)
        .join(UserOrganization, (UserOrganization.org_id == StudyOwnerAssignment.org_id)
              & (UserOrganization.user_id == StudyOwnerAssignment.user_id))
        .where(
            StudyOwnerAssignment.org_id == org.id,
            StudyOwnerAssignment.owner_uuid.in_((lead, backup)),
            StudyOwnerAssignment.responsibility == StudyOwnerFunction.SUPPORT.value,
            StudyOwnerAssignment.scope_kind == "organization",
            StudyOwnerAssignment.scope_uuid == org.org_uuid,
            StudyOwnerAssignment.status == "active",
            StudyOwnerAssignment.effective_from <= now,
            or_(StudyOwnerAssignment.effective_to.is_(None), StudyOwnerAssignment.effective_to > now),
        )
    )).tuples().all()
    if len(rows) != len({user_id for _, user_id in rows}):
        return ()
    owners = dict(rows)
    return tuple(owners[key] for key in (lead, backup) if key in owners)


async def _support_guidance(db: AsyncSession, org: Organization) -> tuple[PublicSupportGuidance, list[int], tuple[int, ...]]:
    try:
        now = datetime.now(UTC)
        service, accessibility = await _current_policies(
            db, org, (StudyPolicyKey.D_04, StudyPolicyKey.D_15), now,
        )
        staff = await _support_staff(db, org, service, now) if _support_intake_enabled() else ()
        routes = _policy_area_values(service, "support_routes")
        hours = _policy_area_values(service, "monitored_hours")
        contact = routes.get("public_support")
        if not isinstance(contact, dict):
            raise HTTPException(404, "Public support guidance is unavailable.")
        try:
            response_expectations = _PublicResponseExpectation.validate_python(
                _policy_area_values(service, "response_targets").get("public_support")
            )
        except ValidationError:
            response_expectations = None
        guidance = PublicSupportGuidance.model_validate({
            "categories": contact.get("categories"),
            "contact_instructions": contact.get("contact_instructions"),
            "monitored_hours": hours.get("public_support"),
            "service_limits": contact.get("service_limits"),
            "response_expectations": response_expectations,
            "intake_available": len(staff) == 2 and response_expectations is not None,
        })
        return guidance, [int(service.id), int(accessibility.id)], staff
    except ValidationError:
        raise HTTPException(404, "Public support guidance is unavailable.") from None
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        raise HTTPException(404, "Public support guidance is unavailable.") from None


async def public_support_guidance(db: AsyncSession, org: Organization) -> PublicSupportGuidance:
    guidance, policy_ids, _ = await _support_guidance(db, org)
    if guidance.intake_available and os.getenv("LEARNHOUSE_STUDYTOMY_GUEST_SUPPORT_ENABLED") == "true":
        service = await db.get(StudyPolicyVersion, policy_ids[0])
        if service is not None:
            guidance.guest_verification_available = await _guest_verification_available(
                db, org, service, datetime.now(UTC),
            )
    return guidance


class SupportCaseCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    topic: _Category
    subject_user_uuids: Annotated[
        list[Annotated[str, StringConstraints(strict=True, pattern=r"^user_[a-zA-Z0-9_-]{1,95}$")]],
        Field(max_length=10),
    ] = Field(default_factory=list)


class SupportCaseReceipt(BaseModel):
    case_uuid: UUID
    topic: str
    status: str


async def open_support_case(
    db: AsyncSession, org: Organization, actor: PublicUser, command: SupportCaseCommand,
) -> SupportCaseReceipt:
    await lock_organization(db, org.id)
    await require_org_member_row(db, org.id, actor.id)
    guidance, policy_ids, staff = await _support_guidance(db, org)
    operation = "studytomy.support.open_case"
    key = str(command.request_id)
    subjects = sorted(set(command.subject_user_uuids))
    payload = {"topic": command.topic}
    if subjects:
        payload["subject_user_uuids"] = subjects
    replay = await claim_idempotency(
        db, org_id=org.id, actor_user_id=actor.id, operation=operation,
        idempotency_key=key, request_payload=payload,
    )
    if replay is not None:
        return SupportCaseReceipt.model_validate(replay.response_projection)
    if not guidance.intake_available:
        raise HTTPException(409, "Online support intake is unavailable.")
    if command.topic not in guidance.categories:
        raise HTTPException(422, "Choose a topic from the current support guidance.")

    excluded = await resolve_case_subjects(db, org, subjects)
    owner_user_id = next((user_id for user_id in staff if user_id != actor.id and user_id not in excluded), None)
    if owner_user_id is None:
        raise HTTPException(409, "Use the approved support contact for independent review.")
    case = StudyServiceCase(
        org_id=org.id, reporter_user_id=actor.id, category="support", support_topic=command.topic,
        owner_user_id=owner_user_id, status="assigned",
    )
    db.add(case)
    await db.flush()
    db.add_all([
        StudyServiceCaseExclusion(org_id=org.id, case_uuid=case.case_uuid, user_id=user_id)
        for user_id in excluded
    ])
    receipt = SupportCaseReceipt(case_uuid=UUID(case.case_uuid), topic=command.topic, status=case.status)
    await append_business_event(
        db, org_id=org.id, actor_user_id=actor.id, resource_kind="case", resource_uuid=case.case_uuid,
        action=operation, reason_code="SUPPORT_CASE_ASSIGNED",
        after_state={"status": case.status, "case_owner_user_id": owner_user_id}, policy_refs=policy_ids,
    )
    await complete_idempotency(
        db, org_id=org.id, actor_user_id=actor.id, operation=operation, idempotency_key=key,
        resource_kind="case", resource_uuid=case.case_uuid, response_status=201,
        response_projection=receipt.model_dump(mode="json"),
    )
    return receipt


async def own_support_case(
    db: AsyncSession, org: Organization, actor: PublicUser, case_uuid: UUID,
) -> SupportCaseReceipt:
    case = (await db.exec(
        select(StudyServiceCase)
        .join(UserOrganization, (UserOrganization.org_id == StudyServiceCase.org_id)
              & (UserOrganization.user_id == StudyServiceCase.reporter_user_id))
        .where(
            StudyServiceCase.org_id == org.id, StudyServiceCase.case_uuid == str(case_uuid),
            StudyServiceCase.category == "support", StudyServiceCase.reporter_user_id == actor.id,
            StudyServiceCase.support_topic.is_not(None),
        )
    )).one_or_none()
    if case is None:
        raise HTTPException(404, "Support case not found.")
    return SupportCaseReceipt(case_uuid=case_uuid, topic=case.support_topic, status=case.status)


class StaffSupportCase(SupportCaseReceipt):
    version: int
    allowed_transitions: list[str]


class SupportTransition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Annotated[int, Field(strict=True, ge=1, le=2147483647)]
    next_status: Literal["awaiting_information", "under_review", "resolved", "closed"]


_TRANSITIONS = {
    "assigned": {"awaiting_information", "under_review"},
    "awaiting_information": {"under_review"},
    "under_review": {"awaiting_information", "resolved"},
    "resolved": {"under_review", "closed"},
}


async def _staff_context(
    db: AsyncSession, org: Organization, actor: PublicUser,
) -> tuple[list[int], tuple[int, ...]]:
    now = datetime.now(UTC)
    try:
        service, accessibility = await _current_policies(db, org, (StudyPolicyKey.D_04, StudyPolicyKey.D_15), now)
        staffing = await _support_staff(db, org, service, now)
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        raise HTTPException(404, "Support case not found.") from None
    if actor.id not in staffing:
        raise HTTPException(404, "Support case not found.")
    return [int(service.id), int(accessibility.id)], staffing


def _case_exclusion(org_id: int, user_id):
    return select(StudyServiceCaseExclusion.user_id).where(
        StudyServiceCaseExclusion.org_id == org_id,
        StudyServiceCaseExclusion.case_uuid == StudyServiceCase.case_uuid,
        StudyServiceCaseExclusion.user_id == user_id,
    ).exists()


def _staff_case_scope(org_id: int, actor_id: int):
    return (
        StudyServiceCase.org_id == org_id,
        StudyServiceCase.category == "support",
        StudyServiceCase.support_topic.is_not(None),
        StudyServiceCase.owner_user_id == actor_id,
        StudyServiceCase.reporter_user_id.is_distinct_from(actor_id),
        ~_case_exclusion(org_id, actor_id),
    )


async def _owned_staff_case(
    db: AsyncSession, org: Organization, actor: PublicUser, reference: UUID,
    *, for_update: bool = False,
) -> tuple[StudyServiceCase, list[int], tuple[int, ...]]:
    statement = select(StudyServiceCase).where(
        *_staff_case_scope(org.id, actor.id),
        StudyServiceCase.case_uuid == str(reference),
    )
    if for_update:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    case = (await db.exec(statement)).one_or_none()
    if case is None:
        raise HTTPException(404, "Support case not found.")
    policy_refs, staffing = await _staff_context(db, org, actor)
    return case, policy_refs, staffing


def _staff_case_receipt(case: StudyServiceCase) -> StaffSupportCase:
    return StaffSupportCase(
        case_uuid=UUID(case.case_uuid), topic=case.support_topic, status=case.status, version=case.version,
        allowed_transitions=sorted(_TRANSITIONS.get(case.status, ())),
    )


async def read_staff_support_case(
    db: AsyncSession, org: Organization, actor: PublicUser, case_uuid: UUID,
) -> StaffSupportCase:
    case, _, _ = await _owned_staff_case(db, org, actor, case_uuid)
    return _staff_case_receipt(case)


async def transition_support_case(
    db: AsyncSession, org: Organization, actor: PublicUser, case_uuid: UUID, command: SupportTransition,
) -> StaffSupportCase:
    await lock_organization(db, org.id)
    case, policy_ids, _ = await _owned_staff_case(db, org, actor, case_uuid, for_update=True)
    if case.version != command.expected_version:
        raise HTTPException(409, "The case changed. Reload it before updating the status.")
    if command.next_status not in _TRANSITIONS.get(case.status, ()):
        raise HTTPException(409, "The requested case transition is unavailable.")
    before = {"status": case.status, "version": case.version}
    case.status = command.next_status
    case.version += 1
    await append_business_event(
        db, org_id=org.id, actor_user_id=actor.id, actor_capacity="support_case_owner",
        resource_kind="case", resource_uuid=case.case_uuid,
        action="studytomy.support.transition_case", reason_code="SUPPORT_CASE_TRANSITION",
        before_state=before, after_state={"status": case.status, "version": case.version},
        policy_refs=policy_ids,
    )
    return _staff_case_receipt(case)


class StaffSupportQueue(BaseModel):
    cases: list[StaffSupportCase]
    next_cursor: UUID | None


def _recoverable_case_scope(org_id: int, actor_id: int, staffing: tuple[int, ...]):
    return (
        StudyServiceCase.org_id == org_id,
        StudyServiceCase.category == "support",
        StudyServiceCase.support_topic.is_not(None),
        StudyServiceCase.owner_user_id.is_not(None),
        StudyServiceCase.reporter_user_id.is_distinct_from(actor_id),
        StudyServiceCase.status != "closed",
        ~_case_exclusion(org_id, actor_id),
        or_(
            StudyServiceCase.owner_user_id.not_in(staffing),
            _case_exclusion(org_id, StudyServiceCase.owner_user_id),
        ),
    )


async def list_staff_support_cases(
    db: AsyncSession, org: Organization, actor: PublicUser, after: UUID | None,
    *, view: Literal["assigned", "recoverable"] = "assigned",
) -> StaffSupportQueue:
    _, staffing = await _staff_context(db, org, actor)
    if view == "recoverable":
        scope = _recoverable_case_scope(org.id, actor.id, staffing)
    else:
        scope = (*_staff_case_scope(org.id, actor.id), StudyServiceCase.status != "closed")
    statement = select(StudyServiceCase).where(*scope)
    if after is not None:
        statement = statement.where(StudyServiceCase.case_uuid > str(after))
    page_size = 25
    rows = (await db.exec(statement.order_by(StudyServiceCase.case_uuid).limit(page_size + 1))).all()
    page = rows[:page_size]
    return StaffSupportQueue(
        cases=[
            _staff_case_receipt(case).model_copy(update={"allowed_transitions": []})
            if view == "recoverable" else _staff_case_receipt(case)
            for case in page
        ],
        next_cursor=UUID(page[-1].case_uuid) if len(rows) > page_size else None,
    )


class SupportHandoff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Annotated[int, Field(strict=True, ge=1, le=2147483647)]


async def handoff_support_case(
    db: AsyncSession, org: Organization, actor: PublicUser, case_uuid: UUID, command: SupportHandoff,
) -> SupportCaseReceipt:
    await lock_organization(db, org.id)
    case, policy_ids, staffing = await _owned_staff_case(db, org, actor, case_uuid, for_update=True)
    if case.version != command.expected_version:
        raise HTTPException(409, "The case changed. Reload it before handing it off.")
    if case.status == "closed":
        raise HTTPException(409, "Closed cases cannot be handed off.")
    incoming_id = next((user_id for user_id in staffing if user_id != actor.id), None)
    if incoming_id is None or incoming_id == case.reporter_user_id or await db.get(
        StudyServiceCaseExclusion, (org.id, case.case_uuid, incoming_id),
    ) is not None:
        raise HTTPException(409, "The approved alternate cannot independently review this case.")
    await _record_owner_change(db, org, actor, case, incoming_id, policy_ids, recovery=False)
    return SupportCaseReceipt(case_uuid=case_uuid, topic=case.support_topic, status=case.status)


async def recover_support_case(
    db: AsyncSession, org: Organization, actor: PublicUser, case_uuid: UUID, command: SupportHandoff,
) -> StaffSupportCase:
    await lock_organization(db, org.id)
    policy_ids, staffing = await _staff_context(db, org, actor)
    case = (await db.exec(select(StudyServiceCase).where(
        *_recoverable_case_scope(org.id, actor.id, staffing),
        StudyServiceCase.case_uuid == str(case_uuid),
    ).with_for_update().execution_options(populate_existing=True))).one_or_none()
    if case is None:
        raise HTTPException(404, "Support case not found.")
    if case.version != command.expected_version:
        raise HTTPException(409, "The case changed. Reload it before restoring ownership.")
    await _record_owner_change(db, org, actor, case, actor.id, policy_ids, recovery=True)
    return _staff_case_receipt(case)


async def _record_owner_change(
    db: AsyncSession, org: Organization, actor: PublicUser, case: StudyServiceCase,
    incoming_id: int, policy_ids: list[int], *, recovery: bool,
) -> None:
    before = {"case_owner_user_id": case.owner_user_id, "version": case.version}
    case.owner_user_id = incoming_id
    case.version += 1
    await append_business_event(
        db, org_id=org.id, actor_user_id=actor.id, actor_capacity="support_case_owner",
        resource_kind="case", resource_uuid=case.case_uuid,
        action="studytomy.support.recover_case" if recovery else "studytomy.support.handoff_case",
        reason_code="SUPPORT_CASE_RECOVERED" if recovery else "SUPPORT_CASE_HANDED_OFF",
        before_state=before, after_state={"case_owner_user_id": incoming_id, "version": case.version},
        policy_refs=policy_ids,
    )
