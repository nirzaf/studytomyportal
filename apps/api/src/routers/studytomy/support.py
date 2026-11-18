"""Public support information independent of lesson access."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import AwareDatetime
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.users import PublicUser
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.services.studytomy.metrics import SupportOperatingReport, read_support_operating_report
from src.services.studytomy.support import (
    PublicSupportGuidance, StaffSupportCase, StaffSupportQueue, SupportCaseCommand, SupportCaseReceipt, SupportHandoff, SupportTransition,
    handoff_support_case, list_staff_support_cases, open_support_case, own_support_case, public_support_guidance, read_staff_support_case, recover_support_case, transition_support_case,
)

router = APIRouter()


@router.get("/orgs/{org_uuid}/support/public-contact")
async def support_contact(
    org_uuid: str, response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicSupportGuidance:
    org = (await db.exec(select(Organization).where(Organization.org_uuid == org_uuid))).one_or_none()
    if org is None:
        raise HTTPException(404, "Public support guidance is unavailable.")
    guidance = await public_support_guidance(db, org)
    response.headers["Cache-Control"] = "no-store"
    return guidance


async def _case_org(org_uuid: str, db: AsyncSession) -> Organization:
    org = (await db.exec(select(Organization).where(Organization.org_uuid == org_uuid))).one_or_none()
    if org is None:
        raise HTTPException(404, "Support case not found.")
    return org


@router.post("/orgs/{org_uuid}/support/cases", status_code=201)
async def submit_support_case(
    org_uuid: str, command: SupportCaseCommand, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SupportCaseReceipt:
    receipt = await open_support_case(db, await _case_org(org_uuid, db), actor, command)
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.get("/orgs/{org_uuid}/support/cases/{case_uuid}")
async def read_support_case(
    org_uuid: str, case_uuid: UUID, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SupportCaseReceipt:
    receipt = await own_support_case(db, await _case_org(org_uuid, db), actor, case_uuid)
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.get("/orgs/{org_uuid}/support/staff/operating-report")
async def staff_support_operating_report(
    org_uuid: str, start: AwareDatetime, end: AwareDatetime, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SupportOperatingReport:
    report = await read_support_operating_report(
        db, await _case_org(org_uuid, db), actor, start=start, end=end,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return report


@router.get("/orgs/{org_uuid}/support/staff/cases")
async def staff_support_queue(
    org_uuid: str, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    after: UUID | None = None,
    view: Literal["assigned", "recoverable"] = "assigned",
) -> StaffSupportQueue:
    queue = await list_staff_support_cases(db, await _case_org(org_uuid, db), actor, after, view=view)
    response.headers["Cache-Control"] = "private, no-store"
    return queue


@router.get("/orgs/{org_uuid}/support/staff/cases/{case_uuid}")
async def staff_support_case(
    org_uuid: str, case_uuid: UUID, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> StaffSupportCase:
    receipt = await read_staff_support_case(db, await _case_org(org_uuid, db), actor, case_uuid)
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.post("/orgs/{org_uuid}/support/staff/cases/{case_uuid}/transition")
async def staff_support_transition(
    org_uuid: str, case_uuid: UUID, command: SupportTransition, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> StaffSupportCase:
    receipt = await transition_support_case(db, await _case_org(org_uuid, db), actor, case_uuid, command)
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.post("/orgs/{org_uuid}/support/staff/cases/{case_uuid}/handoff")
async def staff_support_handoff(
    org_uuid: str, case_uuid: UUID, command: SupportHandoff, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SupportCaseReceipt:
    receipt = await handoff_support_case(db, await _case_org(org_uuid, db), actor, case_uuid, command)
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.post("/orgs/{org_uuid}/support/staff/cases/{case_uuid}/recover")
async def staff_support_recovery(
    org_uuid: str, case_uuid: UUID, command: SupportHandoff, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> StaffSupportCase:
    receipt = await recover_support_case(db, await _case_org(org_uuid, db), actor, case_uuid, command)
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt
