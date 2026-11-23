"""Self-service view of the account's current Studytomy privacy projection."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, Path, Query, Response, UploadFile
from pydantic import AwareDatetime
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.studytomy.privacy import StudyPrivacyStatusRead
from src.db.users import PublicUser
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.privacy import is_protected_studytomy_learner
from src.services.studytomy.record_delivery import (
    CompletionPreview,
    CompletionReleaseCommand,
    CompletionReleaseReceipt,
    hand_off_my_completion_export,
    preview_completion_export,
    release_completion_export,
)
from src.services.studytomy.record_requests import (
    DecisionOutcome,
    RecordRequestDecisionCommand,
    RecordRequestDecisionReceipt,
    RecordRequestEvidenceReceipt,
    RecordRequestReceipt,
    RecordRequestStatus,
    check_my_learning_record_request,
    decide_learning_record_request,
    list_pending_learning_record_requests,
    read_learning_record_request_for_review,
    read_my_learning_record_request_status,
    read_record_request_decision_options,
    submit_my_learning_record_request,
    upload_record_request_evidence,
)
from src.services.studytomy.retention import (
    CaseEvidenceHoldCommand,
    CaseEvidenceHoldReceipt,
    CaseEvidenceHoldReleaseCommand,
    CaseEvidenceHoldReleaseReceipt,
    EvidenceDryRun,
    EvidenceRemovalApprovalCommand,
    EvidenceRemovalApprovalReceipt,
    approve_case_evidence_removal,
    dry_run_case_evidence_retention,
    place_case_evidence_hold,
    release_case_evidence_hold,
)
from src.services.studytomy.retention_execution import (
    EvidenceRemovalReceipt,
    remove_approved_case_evidence,
    replay_case_evidence_removal,
)
from src.services.studytomy.learning_practice_retention import (
    PracticeRetentionHoldCommand,
    PracticeRetentionReceipt,
    PracticeRetentionReleaseCommand,
    PracticeRetentionRemovalCommand,
    place_practice_retention_hold,
    release_practice_retention_hold,
    remove_practice_surface_manually,
)

router = APIRouter()
Actor = Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)]
Session = Annotated[AsyncSession, Depends(get_db_session)]


async def _record_request_org(db: AsyncSession, org_uuid: str) -> Organization:
    org = (await db.execute(select(Organization).where(Organization.org_uuid == org_uuid))).scalar_one_or_none()
    if org is None:
        raise studytomy_http_error(404, "RECORD_REQUEST_UNAVAILABLE", "Record request unavailable.")
    return org


@router.put("/orgs/{org_uuid}/learning-practice/surfaces/{surface_uuid}/retention-hold")
async def api_hold_learning_practice(
    org_uuid: str, surface_uuid: UUID, command: PracticeRetentionHoldCommand, response: Response,
    current_user: Actor, db_session: Session,
) -> PracticeRetentionReceipt:
    org = await _record_request_org(db_session, org_uuid)
    receipt = await place_practice_retention_hold(db_session, org, current_user, str(surface_uuid), command)
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.put("/orgs/{org_uuid}/learning-practice/surfaces/{surface_uuid}/retention-hold/release")
async def api_release_learning_practice_hold(
    org_uuid: str, surface_uuid: UUID, command: PracticeRetentionReleaseCommand, response: Response,
    current_user: Actor, db_session: Session,
) -> PracticeRetentionReceipt:
    org = await _record_request_org(db_session, org_uuid)
    receipt = await release_practice_retention_hold(db_session, org, current_user, str(surface_uuid), command)
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.post("/orgs/{org_uuid}/learning-practice/surfaces/{surface_uuid}/remove")
async def api_remove_expired_learning_practice(
    org_uuid: str, surface_uuid: UUID, command: PracticeRetentionRemovalCommand, response: Response,
    current_user: Actor, db_session: Session,
) -> PracticeRetentionReceipt:
    org = await _record_request_org(db_session, org_uuid)
    receipt = await remove_practice_surface_manually(db_session, org, current_user, str(surface_uuid), command)
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.get("/orgs/{org_uuid}/learners/me/record-requests/availability", status_code=204)
async def api_check_my_record_request(org_uuid: str, current_user: Actor, db_session: Session) -> Response:
    org = await _record_request_org(db_session, org_uuid)
    await check_my_learning_record_request(db_session, org, current_user)
    return Response(status_code=204, headers={"Cache-Control": "private, no-store"})


@router.put("/orgs/{org_uuid}/learners/me/record-requests/{request_reference}")
async def api_submit_my_record_request(
    org_uuid: str, request_reference: UUID, response: Response,
    current_user: Actor,
    db_session: Session,
) -> RecordRequestReceipt:
    org = await _record_request_org(db_session, org_uuid)
    receipt = await submit_my_learning_record_request(db_session, org, current_user, request_reference)
    await db_session.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.get("/orgs/{org_uuid}/learners/me/record-requests/{request_reference}")
async def api_read_my_record_request_status(
    org_uuid: str, request_reference: UUID, response: Response, current_user: Actor, db_session: Session,
) -> RecordRequestStatus:
    org = await _record_request_org(db_session, org_uuid)
    result = await read_my_learning_record_request_status(db_session, org, current_user, request_reference)
    response.headers["Cache-Control"] = "private, no-store"
    return result


@router.get("/orgs/{org_uuid}/record-requests")
async def api_list_pending_record_requests(
    org_uuid: str, response: Response, current_user: Actor, db_session: Session,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[RecordRequestReceipt]:
    org = await _record_request_org(db_session, org_uuid)
    receipts = await list_pending_learning_record_requests(db_session, org, current_user, limit=limit)
    response.headers["Cache-Control"] = "private, no-store"
    return receipts


@router.get("/orgs/{org_uuid}/record-requests/{request_reference}")
async def api_read_record_request_for_review(
    org_uuid: str, request_reference: UUID, response: Response,
    current_user: Actor, db_session: Session,
) -> RecordRequestReceipt:
    org = await _record_request_org(db_session, org_uuid)
    receipt = await read_learning_record_request_for_review(db_session, org, current_user, request_reference)
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.put("/orgs/{org_uuid}/record-requests/{request_reference}/decision")
async def api_decide_record_request(
    org_uuid: str, request_reference: UUID, command: RecordRequestDecisionCommand, response: Response,
    current_user: Actor, db_session: Session,
) -> RecordRequestDecisionReceipt:
    org = await _record_request_org(db_session, org_uuid)
    receipt = await decide_learning_record_request(db_session, org, current_user, request_reference, command)
    await db_session.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.get("/orgs/{org_uuid}/record-requests/{request_reference}/decision")
async def api_get_record_request_decision_options(
    org_uuid: str, request_reference: UUID, response: Response, current_user: Actor, db_session: Session,
) -> dict[DecisionOutcome, list[str]]:
    org = await _record_request_org(db_session, org_uuid)
    options = await read_record_request_decision_options(db_session, org, current_user, request_reference)
    response.headers["Cache-Control"] = "private, no-store"
    return options


@router.post("/orgs/{org_uuid}/record-requests/{request_reference}/evidence", status_code=201)
async def api_upload_record_request_evidence(
    org_uuid: str, request_reference: UUID, response: Response, current_user: Actor, db_session: Session,
    file: Annotated[UploadFile, File()],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> RecordRequestEvidenceReceipt:
    org = await _record_request_org(db_session, org_uuid)
    result = await upload_record_request_evidence(db_session, org, current_user, request_reference, file, idempotency_key)
    response.headers["Cache-Control"] = "private, no-store"
    return result


@router.get("/orgs/{org_uuid}/record-requests/{request_reference}/completion-export")
async def api_preview_completion_export(
    org_uuid: str, request_reference: UUID, response: Response, current_user: Actor, db_session: Session,
) -> CompletionPreview:
    org = await _record_request_org(db_session, org_uuid)
    preview = await preview_completion_export(db_session, org, current_user, request_reference)
    response.headers["Cache-Control"] = "private, no-store"
    return preview


@router.put("/orgs/{org_uuid}/record-requests/{request_reference}/completion-export")
async def api_release_completion_export(
    org_uuid: str, request_reference: UUID, command: CompletionReleaseCommand, response: Response,
    current_user: Actor, db_session: Session,
) -> CompletionReleaseReceipt:
    org = await _record_request_org(db_session, org_uuid)
    receipt = await release_completion_export(db_session, org, current_user, request_reference, command)
    await db_session.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.post("/orgs/{org_uuid}/learners/me/record-requests/{request_reference}/completion-export")
async def api_download_completion_export(
    org_uuid: str, request_reference: UUID, current_user: Actor, db_session: Session,
) -> Response:
    org = await _record_request_org(db_session, org_uuid)
    export, receipt = await hand_off_my_completion_export(db_session, org, current_user, request_reference)
    attachment = Response(
        content=export.model_dump_json(), media_type="application/json",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": 'attachment; filename="learning-completions.json"',
            "X-Content-Type-Options": "nosniff",
            "X-Studytomy-Delivery-Receipt": receipt,
        },
    )
    await db_session.commit()
    return attachment


@router.put("/orgs/{org_uuid}/record-requests/{request_reference}/evidence-holds/{hold_reference}")
async def api_place_case_evidence_hold(
    org_uuid: str, request_reference: UUID, hold_reference: UUID, command: CaseEvidenceHoldCommand,
    response: Response, current_user: Actor, db_session: Session,
) -> CaseEvidenceHoldReceipt:
    org = await _record_request_org(db_session, org_uuid)
    receipt = await place_case_evidence_hold(db_session, org, current_user, request_reference, hold_reference, command)
    await db_session.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.put("/orgs/{org_uuid}/record-requests/{request_reference}/evidence-holds/{hold_reference}/release")
async def api_release_case_evidence_hold(
    org_uuid: str, request_reference: UUID, hold_reference: UUID, command: CaseEvidenceHoldReleaseCommand,
    response: Response, current_user: Actor, db_session: Session,
) -> CaseEvidenceHoldReleaseReceipt:
    org = await _record_request_org(db_session, org_uuid)
    receipt = await release_case_evidence_hold(db_session, org, current_user, request_reference, hold_reference, command)
    await db_session.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.get("/orgs/{org_uuid}/record-requests/{request_reference}/evidence-retention")
async def api_dry_run_case_evidence_retention(
    org_uuid: str, request_reference: UUID, response: Response, current_user: Actor, db_session: Session,
    as_of: AwareDatetime,
) -> EvidenceDryRun:
    org = await _record_request_org(db_session, org_uuid)
    result = await dry_run_case_evidence_retention(db_session, org, current_user, request_reference, as_of)
    response.headers["Cache-Control"] = "private, no-store"
    return result


@router.put("/orgs/{org_uuid}/record-requests/{request_reference}/evidence-retention/approvals/{approval_reference}")
async def api_approve_case_evidence_removal(
    org_uuid: str, request_reference: UUID, approval_reference: UUID, command: EvidenceRemovalApprovalCommand,
    response: Response, current_user: Actor, db_session: Session,
) -> EvidenceRemovalApprovalReceipt:
    org = await _record_request_org(db_session, org_uuid)
    receipt = await approve_case_evidence_removal(db_session, org, current_user, request_reference, approval_reference, command)
    await db_session.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.put("/orgs/{org_uuid}/record-requests/{request_reference}/evidence-retention/approvals/{approval_reference}/files/{evidence_reference}")
async def api_remove_approved_case_evidence(
    org_uuid: str, request_reference: UUID, approval_reference: UUID, response: Response,
    current_user: Actor, db_session: Session,
    evidence_reference: Annotated[str, Path(pattern=r"^ev_[A-Za-z0-9_-]{16,80}$")],
) -> EvidenceRemovalReceipt:
    org = await _record_request_org(db_session, org_uuid)
    receipt = await remove_approved_case_evidence(
        db_session, org, current_user, request_reference, approval_reference, evidence_reference,
    )
    await db_session.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.put("/orgs/{org_uuid}/record-requests/{request_reference}/evidence-retention/approvals/{approval_reference}/files/{evidence_reference}/restore-replays/{replay_reference}")
async def api_replay_case_evidence_removal(
    org_uuid: str, request_reference: UUID, approval_reference: UUID, replay_reference: UUID, response: Response,
    current_user: Actor, db_session: Session, evidence_reference: Annotated[str, Path(pattern=r"^ev_[A-Za-z0-9_-]{16,80}$")],
) -> EvidenceRemovalReceipt:
    org = await _record_request_org(db_session, org_uuid)
    receipt = await replay_case_evidence_removal(
        db_session, org, current_user, request_reference, approval_reference, evidence_reference, replay_reference,
    )
    await db_session.commit()
    response.headers["Cache-Control"] = "private, no-store"
    return receipt


@router.get(
    "/privacy/me",
    response_model=StudyPrivacyStatusRead,
    summary="Read the current account's privacy projection",
)
async def api_get_my_privacy_status(
    current_user: Actor,
    db_session: Session,
) -> StudyPrivacyStatusRead:
    protected = current_user.id is not None and await is_protected_studytomy_learner(
        db_session, current_user.id
    )
    return StudyPrivacyStatusRead(
        learner_profile_protected=protected,
        visibility_mode=(
            "private_default" if protected else "standard_account_profile"
        ),
        publicity_controls_available=False,
    )
