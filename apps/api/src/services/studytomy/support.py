"""Public support guidance projected from approved organization policies."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, Field, StringConstraints, ValidationError
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.governance import StudyPolicyKey
from src.services.studytomy.evidence_files import _policy_area_values
from src.services.studytomy.learners import _current_policies

_Guidance = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=1000)]
_Category = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=100)]


class PublicSupportGuidance(BaseModel):
    categories: Annotated[list[_Category], Field(min_length=1, max_length=12)]
    contact_instructions: _Guidance
    monitored_hours: _Guidance
    service_limits: _Guidance


async def public_support_guidance(db: AsyncSession, org: Organization) -> PublicSupportGuidance:
    try:
        service, _ = await _current_policies(db, org, (StudyPolicyKey.D_04, StudyPolicyKey.D_15), datetime.now(UTC))
        routes = _policy_area_values(service, "support_routes")
        hours = _policy_area_values(service, "monitored_hours")
        contact = routes.get("public_support")
        if not isinstance(contact, dict):
            raise HTTPException(404, "Public support guidance is unavailable.")
        return PublicSupportGuidance.model_validate({
            "categories": contact.get("categories"),
            "contact_instructions": contact.get("contact_instructions"),
            "monitored_hours": hours.get("public_support"),
            "service_limits": contact.get("service_limits"),
        })
    except ValidationError:
        raise HTTPException(404, "Public support guidance is unavailable.") from None
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
        raise HTTPException(404, "Public support guidance is unavailable.") from None
