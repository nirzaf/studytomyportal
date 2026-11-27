"""Private case metadata for independently assigned safeguarding reviewers."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, HTTPException, Path, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.studytomy.cases import StudyServiceCase
from src.db.users import PublicUser
from src.routers.studytomy.files import _evidence_download
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.services.studytomy.cases import (
    PublicSafeguardingContact,
    SafeguardingReviewStatus,
    _SAFEGUARDING_TRANSITIONS,
    assign_safeguarding_case,
    get_own_safeguarding_report_status,
    list_safeguarding_triage,
    list_unassigned_safeguarding_cases,
    open_safeguarding_case,
    open_safeguarding_case_evidence,
    public_safeguarding_contact,
    read_safeguarding_narrative,
    record_safeguarding_triage,
    safeguarding_reporting_available,
    require_safeguarding_case_review,
    transition_safeguarding_case,
    upload_safeguarding_case_evidence,
)

from src.services.studytomy.retention import (
    CaseEvidenceHoldCommand, CaseEvidenceHoldReceipt,
    CaseEvidenceHoldReleaseCommand, CaseEvidenceHoldReleaseReceipt,
)
from src.services.studytomy.safeguarding_retention import (
    SafeguardingRetentionInventory,
    place_safeguarding_hold,
    release_safeguarding_hold,
    safeguarding_retention_inventory,
)

router = APIRouter()


class SafeguardingCaseSummary(BaseModel):
    case_uuid: UUID
    status: str
    version: int
    assigned_to_me: bool
    can_assign: bool
    allowed_transitions: list[SafeguardingReviewStatus]


class VersionedCaseCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Annotated[int, Field(strict=True, ge=1, le=2147483647)]


class SafeguardingTransition(VersionedCaseCommand):
    next_status: SafeguardingReviewStatus


class SafeguardingAssignment(VersionedCaseCommand):
    next_owner_uuid: Annotated[str, Field(strict=True, pattern=r"^user_[a-zA-Z0-9_-]{1,95}$")] | None = None


class SafeguardingReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: UUID
    narrative: Annotated[str, Field(strict=True, min_length=1, max_length=8000)]
    subject_user_uuids: Annotated[
        list[Annotated[str, Field(strict=True, pattern=r"^user_[a-zA-Z0-9_-]{1,95}$")]],
        Field(max_length=10),
    ] = Field(default_factory=list)


async def _resolve_org(org_uuid: str, db: AsyncSession) -> Organization:
    organization = (await db.exec(
        select(Organization).where(Organization.org_uuid == org_uuid)
    )).one_or_none()
    if organization is None:
        raise HTTPException(404, "Case not found.")
    return organization


def _summary(case: StudyServiceCase, actor_id: int, response: Response) -> SafeguardingCaseSummary:
    response.headers["Cache-Control"] = "private, no-store"
    return SafeguardingCaseSummary(
        case_uuid=UUID(case.case_uuid), status=case.status, version=case.version,
        assigned_to_me=case.owner_user_id == actor_id,
        can_assign=case.owner_user_id in (None, actor_id) and case.status not in ("resolved", "closed"),
        allowed_transitions=sorted(_SAFEGUARDING_TRANSITIONS.get(case.status, ()))
        if case.owner_user_id == actor_id else [],
    )


@router.get("/orgs/{org_uuid}/safeguarding/unassigned-cases")
async def unassigned_cases(
    org_uuid: str, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[SafeguardingCaseSummary]:
    org = await _resolve_org(org_uuid, db)
    cases = await list_unassigned_safeguarding_cases(db, org, actor)
    response.headers["Cache-Control"] = "private, no-store"
    return [_summary(case, actor.id, response) for case in cases]


@router.get("/orgs/{org_uuid}/safeguarding/cases/{case_uuid}")
async def case_summary(
    org_uuid: str, case_uuid: UUID, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SafeguardingCaseSummary:
    org = await _resolve_org(org_uuid, db)
    case = await require_safeguarding_case_review(db, org, actor, case_uuid)
    return _summary(case, actor.id, response)


@router.post("/orgs/{org_uuid}/safeguarding/cases/{case_uuid}/transition")
async def transition_case(
    org_uuid: str, case_uuid: UUID, command: SafeguardingTransition, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SafeguardingCaseSummary:
    org = await _resolve_org(org_uuid, db)
    case = await transition_safeguarding_case(
        db, org, actor, case_uuid,
        expected_version=command.expected_version, next_status=command.next_status,
    )
    summary = _summary(case, actor.id, response)
    await db.commit()
    return summary


@router.post("/orgs/{org_uuid}/safeguarding/cases/{case_uuid}/assignment")
async def assign_case(
    org_uuid: str, case_uuid: UUID, command: SafeguardingAssignment, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SafeguardingCaseSummary:
    org = await _resolve_org(org_uuid, db)
    case = await assign_safeguarding_case(
        db, org, actor, case_uuid,
        expected_version=command.expected_version, next_owner_uuid=command.next_owner_uuid,
    )
    summary = _summary(case, actor.id, response)
    await db.commit()
    return summary


@router.get("/orgs/{org_uuid}/safeguarding/cases/{case_uuid}/evidence/{evidence_uuid}")
async def download_case_evidence(
    org_uuid: str, case_uuid: UUID,
    evidence_uuid: Annotated[str, Path(pattern=r"^ev_[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")],
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> StreamingResponse:
    org = await _resolve_org(org_uuid, db)
    media_type, content = await open_safeguarding_case_evidence(db, org, actor, case_uuid, evidence_uuid)
    return _evidence_download(evidence_uuid, media_type, content)


@router.post("/orgs/{org_uuid}/safeguarding/cases/{case_uuid}/evidence", status_code=201)
async def attach_case_evidence(
    org_uuid: str, case_uuid: UUID, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> dict[str, str]:
    org = await _resolve_org(org_uuid, db)
    evidence_uuid = await upload_safeguarding_case_evidence(db, org, actor, case_uuid, file, idempotency_key)
    response.headers["Cache-Control"] = "private, no-store"
    return {"evidence_uuid": evidence_uuid}


@router.post("/orgs/{org_uuid}/safeguarding/reports", status_code=201)
async def submit_safeguarding_report(
    org_uuid: str, command: SafeguardingReport, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    org = await _resolve_org(org_uuid, db)
    receipt = await open_safeguarding_case(
        db, org, actor, request_id=command.request_id,
        subject_user_uuids=command.subject_user_uuids, narrative=command.narrative,
    )
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.get("/orgs/{org_uuid}/safeguarding/cases/{case_uuid}/narrative")
async def case_narrative(
    org_uuid: str, case_uuid: UUID, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    org = await _resolve_org(org_uuid, db)
    narrative = await read_safeguarding_narrative(db, org, actor, case_uuid)
    response.headers["Cache-Control"] = "private, no-store"
    return {"narrative": narrative}


@router.get("/orgs/{org_uuid}/safeguarding/public-contact")
async def public_contact(
    org_uuid: str, response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicSafeguardingContact:
    org = await _resolve_org(org_uuid, db)
    contact = await public_safeguarding_contact(db, org)
    response.headers["Cache-Control"] = "no-store"
    return contact


class SafeguardingReportStatus(BaseModel):
    case_uuid: UUID
    status: str


@router.get("/orgs/{org_uuid}/safeguarding/reports/{case_uuid}")
async def own_report_status(
    org_uuid: str, case_uuid: UUID, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SafeguardingReportStatus:
    org = (await db.exec(select(Organization).where(Organization.org_uuid == org_uuid))).one_or_none()
    if org is None:
        raise HTTPException(404, "Report not found.")
    status = await get_own_safeguarding_report_status(db, org, actor, case_uuid)
    response.headers["Cache-Control"] = "private, no-store"
    return SafeguardingReportStatus(case_uuid=case_uuid, status=status)


@router.get("/orgs/{org_uuid}/safeguarding/reporting-availability")
async def reporting_availability(
    org_uuid: str, response: Response,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, bool]:
    org = await _resolve_org(org_uuid, db)
    response.headers["Cache-Control"] = "no-store"
    return {"available": await safeguarding_reporting_available(db, org)}


class SafeguardingTriageItem(BaseModel):
    case_uuid: UUID
    version: int


@router.get("/orgs/{org_uuid}/safeguarding/triage")
async def safeguarding_triage(
    org_uuid: str, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    after: UUID | None = None,
) -> list[SafeguardingTriageItem]:
    org = await _resolve_org(org_uuid, db)
    rows = await list_safeguarding_triage(db, org, actor, after=after)
    response.headers["Cache-Control"] = "private, no-store"
    return [SafeguardingTriageItem(case_uuid=UUID(case_uuid), version=version) for case_uuid, version in rows]


class SafeguardingTriageCommand(VersionedCaseCommand):
    verification_reference: UUID
    subject_user_uuids: Annotated[
        list[Annotated[str, Field(strict=True, pattern=r"^user_[a-zA-Z0-9_-]{1,95}$")]],
        Field(max_length=10),
    ]


@router.post("/orgs/{org_uuid}/safeguarding/triage/{case_uuid}")
async def submit_triage(
    org_uuid: str, case_uuid: UUID, command: SafeguardingTriageCommand, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SafeguardingCaseSummary:
    org = await _resolve_org(org_uuid, db)
    case = await record_safeguarding_triage(
        db, org, actor, case_uuid, expected_version=command.expected_version,
        verification_reference=command.verification_reference, subject_user_uuids=command.subject_user_uuids,
    )
    summary = _summary(case, actor.id, response)
    await db.commit()
    return summary


@router.get("/orgs/{org_uuid}/safeguarding/cases/{case_uuid}/retention-inventory")
async def case_retention_inventory(
    org_uuid: str, case_uuid: UUID, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SafeguardingRetentionInventory:
    org = await _resolve_org(org_uuid, db)
    inventory = await safeguarding_retention_inventory(db, org, actor, case_uuid)
    response.headers["Cache-Control"] = "private, no-store"
    return inventory


@router.put("/orgs/{org_uuid}/safeguarding/cases/{case_uuid}/retention-holds/{hold_uuid}")
async def place_case_retention_hold(
    org_uuid: str, case_uuid: UUID, hold_uuid: UUID, command: CaseEvidenceHoldCommand, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CaseEvidenceHoldReceipt:
    org = await _resolve_org(org_uuid, db)
    result = await place_safeguarding_hold(db, org, actor, case_uuid, hold_uuid, command)
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return result


@router.put("/orgs/{org_uuid}/safeguarding/cases/{case_uuid}/retention-holds/{hold_uuid}/release")
async def release_case_retention_hold(
    org_uuid: str, case_uuid: UUID, hold_uuid: UUID, command: CaseEvidenceHoldReleaseCommand, response: Response,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> CaseEvidenceHoldReleaseReceipt:
    org = await _resolve_org(org_uuid, db)
    result = await release_safeguarding_hold(db, org, actor, case_uuid, hold_uuid, command)
    await db.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return result
