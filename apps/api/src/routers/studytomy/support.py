"""Public support information independent of lesson access."""

import asyncio
import hashlib
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, EmailStr, Field, TypeAdapter, ValidationError
from redis.exceptions import RedisError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.users import PublicUser
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.services.security.rate_limiting import get_client_ip, get_redis_connection
from src.services.studytomy.guest_support import (
    deliver_guest_support_challenge, guest_contact_bucket, issue_guest_support_challenge,
    redeem_guest_support_challenge,
)
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


class GuestSupportRedemption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: Annotated[str, Field(max_length=128, repr=False)]


def _consume_guest_limit(key: str, limit: int, seconds: int) -> bool:
    script = """
    local used = tonumber(redis.call('GET', KEYS[1]) or '0')
    if used >= tonumber(ARGV[1]) then return 0 end
    if used == 0 then
        redis.call('SET', KEYS[1], 1, 'EX', tonumber(ARGV[2]))
    else
        redis.call('INCR', KEYS[1])
    end
    return 1
    """
    return bool(get_redis_connection().eval(script, 1, f"guest-support:{key}", limit, seconds))


def _allow_guest_redemption(address: str) -> bool:
    key = hashlib.sha256(address.encode()).hexdigest()
    return _consume_guest_limit(f"redeem:{key}", 12, 600)


class GuestSupportIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[str, Field(max_length=320, repr=False)]
    topic: Annotated[str, Field(min_length=1, max_length=100)]


_GUEST_EMAIL = TypeAdapter(EmailStr)


@router.post("/orgs/{org_uuid}/support/guest-challenges", status_code=202)
async def request_guest_support_challenge(
    org_uuid: str, command: GuestSupportIssue, request: Request, background: BackgroundTasks,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    accepted = Response(status_code=202, headers={"Cache-Control": "no-store"})
    try:
        email = str(_GUEST_EMAIL.validate_python(command.email.strip()))
        address = hashlib.sha256(get_client_ip(request).encode()).hexdigest()
        if not await asyncio.to_thread(_consume_guest_limit, f"issue-ip:{address}", 4, 600):
            return accepted
        recipient = guest_contact_bucket(email)
        if not await asyncio.to_thread(_consume_guest_limit, f"issue-recipient:{recipient}", 2, 3600):
            return accepted
    except (HTTPException, RedisError, ValidationError):
        return accepted
    try:
        org = await _case_org(org_uuid, db)
        token = await issue_guest_support_challenge(db, org, email, command.topic)
    except HTTPException as error:
        if error.status_code not in (403, 404, 409):
            raise
        await db.rollback()
    else:
        await db.commit()
        background.add_task(deliver_guest_support_challenge, email, token)
        accepted.background = background
    return accepted


@router.post("/orgs/{org_uuid}/support/guest-challenges/redeem", status_code=202)
async def redeem_guest_support(
    org_uuid: str, command: GuestSupportRedemption, request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    accepted = Response(status_code=202, headers={"Cache-Control": "no-store"})
    # One IP bucket across organizations avoids making tenant existence observable.
    try:
        allowed = await asyncio.to_thread(_allow_guest_redemption, get_client_ip(request))
    except (HTTPException, RedisError):
        return accepted
    if not allowed:
        return accepted
    try:
        org = await _case_org(org_uuid, db)
        await redeem_guest_support_challenge(db, org, command.token)
    except HTTPException as error:
        if error.status_code not in (403, 404, 409):
            raise
        await db.rollback()
    else:
        await db.commit()
    # Neither a valid proof nor its replay reveals the resulting case reference.
    return accepted


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
