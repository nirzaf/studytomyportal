"""Public support information independent of lesson access."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.services.studytomy.support import PublicSupportGuidance, public_support_guidance

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
