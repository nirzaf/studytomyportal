"""Session-only adaptive learning endpoints."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
)
from fastapi.encoders import jsonable_encoder
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.core.events.database import get_db_session
from src.db.organizations import Organization
from src.db.studytomy.adaptive import CurriculumDraftRead, CurriculumReviewedRead
from src.db.users import PublicUser
from src.routers.studytomy.private_route import PRIVATE_HEADERS, PrivateStudyTomyRoute
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.security.org_auth import enforce_org_mfa, get_user_org
from src.security.session_context import POLICY_AUTH_METHODS, get_session_provenance
from src.services.studytomy.assessment_reviews import (
    ArchivedAssessmentResponses,
    AssessmentAppealAvailability,
    AssessmentAppealCorrection,
    AssessmentAppealReceipt,
    AssessmentAppealReopen,
    AssessmentAppealRequest,
    AssessmentDeadlineAdjustment,
    AssessmentDeadlineReceipt,
    AssessmentRetryGrant,
    AssessmentReviewEvidence,
    decide_assessment_appeal,
    read_assessment_review_evidence,
    read_archived_assessment_responses,
    read_latest_assessment_appeal,
    read_own_assessment_appeal,
    record_assessment_deadline_adjustment,
    record_assessment_retry_grant,
    reopen_assessment_appeal,
    request_assessment_appeal,
)
from src.services.studytomy.assessment_archive_retention import ArchiveRemovalReceipt, remove_assessment_archive
from src.services.studytomy.awards import (
    ManagedCertificateChange,
    ManagedCertificateChangeReceipt,
    ManagedCertificateEligibility,
    ManagedCertificateIssue,
    ManagedCertificateIssueReceipt,
    ManagedCertificateVerification,
    issue_managed_certificate,
    preview_managed_certificate_eligibility,
    require_managed_certificate_workspace,
    review_managed_certificate_change,
    verify_my_managed_certificate,
)
from src.services.studytomy.assessment_integrity import (
    IntegrityConcern, IntegrityDismissal, IntegrityReceipt, IntegrityResponse, dismiss_integrity_concern, learner_integrity_case,
    IntegrityIntakeAvailability, IntegrityReviewReceipt, LearnerIntegrityReceipt, list_learner_integrity_cases,
    read_integrity_intake_availability, read_integrity_review,
    record_integrity_concern,
)
from src.services.studytomy.adaptive_access import (
    AdaptiveAvailability,
    get_adaptive_availability,
)
from src.services.studytomy.adaptive_attempts import (
    AttemptRead,
    DiagnosticAbandon,
    DiagnosticReceipt,
    DiagnosticResponse,
    DiagnosticStart,
    DiagnosticSummary,
    LessonAttemptOption,
    PracticeStart,
    abandon_diagnostic,
    lesson_attempt_options,
    read_current_attempt,
    read_diagnostic_attempt,
    read_diagnostic_summary,
    respond_to_diagnostic,
    start_diagnostic,
    start_practice,
)
from src.services.studytomy.adaptive_configuration import (
    AdaptiveConfiguration,
    get_adaptive_configuration,
)
from src.services.studytomy.adaptive_curriculum import (
    ConceptMappingRead,
    ConceptMappingUpdate,
    CurriculumDraftCreate,
    CurriculumDraftUpdate,
    CurriculumGraphRead,
    CurriculumGraphUpdate,
    create_curriculum_draft,
    replace_concept_mappings,
    replace_curriculum_graph,
    update_curriculum_draft,
)
from src.services.studytomy.adaptive_curriculum_review import (
    CurriculumPublish,
    CurriculumRetire,
    CurriculumReviewSnapshot,
    ReviewedCurriculumObjectives,
    ReviewedCurriculumPage,
    curriculum_review_snapshot,
    list_reviewed_course_curricula,
    read_reviewed_curriculum_objectives,
    review_curriculum,
)
from src.services.studytomy.adaptive_diagnostics import (
    DiagnosticDraftCreate,
    DiagnosticStaffRead,
    create_diagnostic,
    read_diagnostic,
    review_diagnostic,
)
from src.services.studytomy.adaptive_evidence import (
    MasteryReconciliationRepair,
    reconcile_mastery,
)
from src.services.studytomy.adaptive_guidance import (
    TutorClose,
    TutorOptions,
    TutorSessionRead,
    TutorStart,
    close_tutor_session,
    read_tutor_options,
    start_tutor_session,
)
from src.services.studytomy.adaptive_item_review import (
    ItemGradingReviewSnapshot,
    ItemReviewedRead,
    ItemReviewSnapshot,
    item_grading_review_snapshot,
    item_review_snapshot,
    review_item,
)
from src.services.studytomy.adaptive_item_source import (
    NativeItemSourceRead,
    read_native_item_source,
)
from src.services.studytomy.adaptive_items import (
    ItemRevisionCreate,
    ItemRevisionStaffRead,
    ItemRevisionUpdate,
    OriginalItemRevisionCreate,
    create_item_revision,
    update_item_revision,
)
from src.services.studytomy.adaptive_progress import (
    MasteryPage,
    NoRecommendation,
    Recommendation,
    read_learner_mastery,
    read_learner_recommendations,
    read_staff_mastery,
)
from src.services.studytomy.adaptive_reports import (
    MyReportsPage,
    PublishedReportRead,
    ReportApproval,
    ReportChange,
    ReportDraftCreate,
    ReportDraftRead,
    ReportEdit,
    ReportPublish,
    ReportPublishReceipt,
    ReportRecipientPage,
    ReportRecipientPreview,
    ReportReviewRequest,
    ReportReviewRequestReceipt,
    ReportWithdrawalReceipt,
    StaffReportList,
    StaffReportReview,
    change_published_report,
    create_report_draft,
    list_my_reports,
    list_report_recipients,
    list_staff_reports,
    preview_report_recipient,
    publish_report,
    read_report,
    read_staff_report_review,
    request_report_review,
    review_report,
)
from src.services.studytomy.adaptive_turns import (
    TutorFeedbackCreate,
    TutorFeedbackReceipt,
    TutorFeedbackReviewCreate,
    TutorFeedbackReviewRead,
    TutorTurnCreate,
    TutorTurnRead,
    create_tutor_turn,
    read_tutor_turn,
    review_tutor_feedback,
    submit_tutor_feedback,
)
from src.services.studytomy.privacy import is_protected_studytomy_learner
from src.services.studytomy.report_notifications import (
    OutboxReconciliationDecision,
    OutboxReconciliationPage,
    OutboxReconciliationResult,
    ReportNotificationRead,
    list_outbox_reconciliation,
    reconcile_outbox_delivery,
    request_report_notification,
)
from src.services.studytomy.idempotency import claim_idempotency, complete_idempotency
from src.services.studytomy.policies import lock_organization

from src.services.studytomy.topic_check import (
    TopicCheckView,
    TopicScope,
    TopicSubmission,
    TopicTimeline,
    read_topic_check,
    read_topic_timeline,
    submit_topic_check,
)


router = APIRouter(route_class=PrivateStudyTomyRoute)


async def adaptive_session_actor(
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
) -> PublicUser:
    provenance = get_session_provenance()
    if provenance is None or provenance.amr not in POLICY_AUTH_METHODS:
        raise HTTPException(
            403,
            detail={
                "code": "INTERACTIVE_SESSION_REQUIRED",
                "message": "Sign in directly to continue.",
            },
            headers=PRIVATE_HEADERS,
        )
    return actor


AdaptiveActor = Annotated[PublicUser, Depends(adaptive_session_actor)]
AdaptiveDB = Annotated[AsyncSession, Depends(get_db_session)]


async def adaptive_organization(
    org_uuid: str,
    response: Response,
    actor: AdaptiveActor,
    db: AdaptiveDB,
) -> Organization:
    result = await db.execute(
        select(Organization).where(Organization.org_uuid == org_uuid)
    )
    org = result.scalar_one_or_none()
    if org is None or await get_user_org(actor.id, org.id, db) is None:
        raise HTTPException(404, "Organization not found", headers=PRIVATE_HEADERS)
    await enforce_org_mfa(actor.id, org.id, db)
    response.headers.update(PRIVATE_HEADERS)
    return org


AdaptiveOrg = Annotated[Organization, Depends(adaptive_organization)]


@router.post("/orgs/{org_uuid}/courses/{course_uuid}/assignments/{assignment_uuid}/submissions/{submission_uuid}/integrity-cases")
async def api_record_assessment_integrity_concern(
    org: AdaptiveOrg, actor: AdaptiveActor, db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    assignment_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    submission_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    body: IntegrityConcern,
    attempt_number: Annotated[int | None, Query(ge=1)] = None,
) -> IntegrityReceipt:
    return await record_integrity_concern(
        db, org, actor, course_uuid, assignment_uuid, submission_uuid, body, attempt_number,
    )


@router.get("/orgs/{org_uuid}/courses/{course_uuid}/assignments/{assignment_uuid}/submissions/{submission_uuid}/integrity-intake")
async def api_read_integrity_intake_availability(
    org: AdaptiveOrg, actor: AdaptiveActor, db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    assignment_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    submission_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    attempt_number: Annotated[int, Query(ge=1)],
) -> IntegrityIntakeAvailability:
    return await read_integrity_intake_availability(
        db, org, actor, course_uuid, assignment_uuid, submission_uuid, attempt_number,
    )


@router.get("/orgs/{org_uuid}/me/assessment-integrity-cases")
async def api_list_learner_assessment_integrity_cases(
    org: AdaptiveOrg, actor: AdaptiveActor, db: AdaptiveDB,
) -> list[LearnerIntegrityReceipt]:
    return await list_learner_integrity_cases(db, org, actor)


@router.get("/orgs/{org_uuid}/courses/{course_uuid}/assignments/{assignment_uuid}/submissions/{submission_uuid}/integrity-cases/{reference}")
@router.get("/orgs/{org_uuid}/courses/{course_uuid}/assignments/{assignment_uuid}/submissions/{submission_uuid}/integrity-cases")
async def api_read_assessment_integrity_review(
    org: AdaptiveOrg, actor: AdaptiveActor, db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    assignment_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    submission_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    reference: UUID | None = None,
    attempt_number: Annotated[int | None, Query(ge=1)] = None,
) -> IntegrityReviewReceipt:
    return await read_integrity_review(
        db, org, actor, course_uuid, assignment_uuid, submission_uuid,
        str(reference) if reference else None, attempt_number,
    )


@router.put("/orgs/{org_uuid}/courses/{course_uuid}/assignments/{assignment_uuid}/submissions/{submission_uuid}/integrity-cases/{reference}/dismissal")
async def api_dismiss_assessment_integrity_concern(
    org: AdaptiveOrg, actor: AdaptiveActor, db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    assignment_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    submission_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    reference: UUID, body: IntegrityDismissal,
    attempt_number: Annotated[int | None, Query(ge=1)] = None,
) -> IntegrityReceipt:
    return await dismiss_integrity_concern(
        db, org, actor, course_uuid, assignment_uuid, submission_uuid,
        str(reference), body, attempt_number,
    )


@router.put("/orgs/{org_uuid}/me/assessment-integrity-cases/{reference}/response")
async def api_respond_to_assessment_integrity_case(
    org: AdaptiveOrg, actor: AdaptiveActor, db: AdaptiveDB, reference: UUID,
    body: IntegrityResponse,
) -> IntegrityReceipt:
    return await learner_integrity_case(db, org, actor, str(reference), body)


@router.post("/orgs/{org_uuid}/courses/{course_uuid}/assignments/{assignment_uuid}/submissions/{submission_uuid}/retry-grants")
async def api_assessment_retry_grant(
    org: AdaptiveOrg, actor: AdaptiveActor, db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    assignment_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    submission_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    body: AssessmentRetryGrant,
    expected_attempt_number: Annotated[int | None, Query(ge=1)] = None,
) -> AssessmentDeadlineReceipt:
    return await record_assessment_retry_grant(
        db, org, actor, course_uuid, assignment_uuid, submission_uuid, body,
        expected_attempt_number=expected_attempt_number,
    )


@router.post("/orgs/{org_uuid}/assessment-archives/{round_uuid}/removal-approvals")
async def api_approve_assessment_archive_removal(
    org: AdaptiveOrg, actor: AdaptiveActor, db: AdaptiveDB,
    round_uuid: Annotated[str, Path(pattern=r"^round_[0-9a-f]{58}$")],
) -> ArchiveRemovalReceipt:
    return await remove_assessment_archive(db, org, actor, round_uuid)


@router.post("/orgs/{org_uuid}/assessment-archives/{round_uuid}/removal-approvals/{approval_uuid}/execute")
async def api_execute_assessment_archive_removal(
    org: AdaptiveOrg, actor: AdaptiveActor, db: AdaptiveDB,
    round_uuid: Annotated[str, Path(pattern=r"^round_[0-9a-f]{58}$")],
    approval_uuid: UUID,
) -> ArchiveRemovalReceipt:
    return await remove_assessment_archive(db, org, actor, round_uuid, str(approval_uuid))


@router.get("/orgs/{org_uuid}/courses/{course_uuid}/assignments/{assignment_uuid}/submissions/{submission_uuid}/attempts/{attempt_number}/archived-responses")
async def api_archived_assessment_responses(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    assignment_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    submission_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    attempt_number: Annotated[int, Path(ge=1)],
) -> ArchivedAssessmentResponses:
    return await read_archived_assessment_responses(
        db, org, actor, course_uuid, assignment_uuid, submission_uuid, attempt_number,
    )


@router.post("/orgs/{org_uuid}/courses/{course_uuid}/assignments/{assignment_uuid}/submissions/{submission_uuid}/deadline-adjustments")
async def api_assessment_deadline_adjustment(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    assignment_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    submission_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    body: AssessmentDeadlineAdjustment,
    expected_attempt_number: Annotated[int | None, Query(ge=1)] = None,
) -> AssessmentDeadlineReceipt:
    return await record_assessment_deadline_adjustment(
        db, org, actor, course_uuid, assignment_uuid, submission_uuid, body,
        expected_attempt_number=expected_attempt_number,
    )


@router.get("/orgs/{org_uuid}/courses/{course_uuid}/assignments/{assignment_uuid}/submissions/{submission_uuid}/review-evidence")
async def api_assessment_review_evidence(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    assignment_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    submission_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    attempt_number: Annotated[int | None, Query(ge=1)] = None,
) -> AssessmentReviewEvidence:
    return await read_assessment_review_evidence(
        db, org, actor, course_uuid, assignment_uuid, submission_uuid,
        attempt_number=attempt_number, include_appeal=True,
    )


@router.get("/orgs/{org_uuid}/courses/{course_uuid}/award-workspace")
async def api_managed_certificate_workspace(
    org: AdaptiveOrg, actor: AdaptiveActor, db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
) -> dict[str, bool]:
    await require_managed_certificate_workspace(db, org, actor, course_uuid)
    return {"available": True}


@router.get("/orgs/{org_uuid}/courses/{course_uuid}/releases/{release_uuid}/learners/{learner_uuid}/award-eligibility")
async def api_managed_certificate_eligibility(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    release_uuid: Annotated[str, Path(min_length=1, max_length=64)],
    learner_uuid: Annotated[str, Path(min_length=1, max_length=100)],
) -> ManagedCertificateEligibility:
    return await preview_managed_certificate_eligibility(db, org, actor, course_uuid, release_uuid, learner_uuid)


@router.post("/orgs/{org_uuid}/courses/{course_uuid}/releases/{release_uuid}/learners/{learner_uuid}/awards")
async def api_issue_managed_certificate(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    release_uuid: Annotated[str, Path(min_length=1, max_length=64)],
    learner_uuid: Annotated[str, Path(min_length=1, max_length=100)],
    body: ManagedCertificateIssue,
) -> ManagedCertificateIssueReceipt:
    receipt = await issue_managed_certificate(db, org, actor, course_uuid, release_uuid, learner_uuid, body)
    await db.commit()
    return receipt


@router.post("/orgs/{org_uuid}/courses/{course_uuid}/releases/{release_uuid}/learners/{learner_uuid}/awards/{award_uuid}/review")
async def api_review_managed_certificate_change(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    release_uuid: Annotated[str, Path(min_length=1, max_length=64)],
    learner_uuid: Annotated[str, Path(min_length=1, max_length=100)],
    award_uuid: UUID,
    body: ManagedCertificateChange,
) -> ManagedCertificateChangeReceipt:
    receipt = await review_managed_certificate_change(
        db, org, actor, course_uuid, release_uuid, learner_uuid, award_uuid, body,
    )
    await db.commit()
    return receipt


@router.get("/orgs/{org_uuid}/me/awards/{award_uuid}")
async def api_verify_my_managed_certificate(
    org: AdaptiveOrg, actor: AdaptiveActor, db: AdaptiveDB, award_uuid: UUID,
) -> ManagedCertificateVerification:
    return await verify_my_managed_certificate(db, org, actor, award_uuid)


@router.post("/orgs/{org_uuid}/courses/{course_uuid}/assignments/{assignment_uuid}/submissions/{submission_uuid}/appeals")
async def api_request_assessment_appeal(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    assignment_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    submission_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    body: AssessmentAppealRequest,
) -> AssessmentAppealReceipt:
    return await request_assessment_appeal(
        db, org, actor, course_uuid, assignment_uuid, submission_uuid, body
    )


@router.get("/orgs/{org_uuid}/courses/{course_uuid}/assignments/{assignment_uuid}/submissions/{submission_uuid}/appeals")
async def api_latest_assessment_appeal(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    assignment_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    submission_uuid: Annotated[str, Path(min_length=1, max_length=255)],
) -> AssessmentAppealAvailability:
    return await read_latest_assessment_appeal(
        db, org, actor, course_uuid, assignment_uuid, submission_uuid,
    )


@router.get("/orgs/{org_uuid}/me/assessment-appeals/{appeal_uuid}")
async def api_assessment_appeal_status(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    appeal_uuid: UUID,
) -> AssessmentAppealReceipt:
    return await read_own_assessment_appeal(db, org, actor, str(appeal_uuid))


@router.post("/orgs/{org_uuid}/courses/{course_uuid}/assignments/{assignment_uuid}/submissions/{submission_uuid}/appeals/{appeal_uuid}/confirm")
async def api_confirm_assessment_appeal(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    assignment_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    submission_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    appeal_uuid: UUID,
    expected_decision_uuid: UUID | None = None,
    attempt_number: Annotated[int | None, Query(ge=1)] = None,
) -> AssessmentAppealReceipt:
    return await decide_assessment_appeal(
        db, org, actor, course_uuid, assignment_uuid, submission_uuid, str(appeal_uuid),
        expected_decision_uuid=expected_decision_uuid,
        attempt_number=attempt_number,
    )


@router.post("/orgs/{org_uuid}/courses/{course_uuid}/assignments/{assignment_uuid}/submissions/{submission_uuid}/appeals/{appeal_uuid}/correct")
async def api_correct_assessment_appeal(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    assignment_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    submission_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    appeal_uuid: UUID,
    body: AssessmentAppealCorrection,
    expected_decision_uuid: UUID | None = None,
    attempt_number: Annotated[int | None, Query(ge=1)] = None,
) -> AssessmentAppealReceipt:
    return await decide_assessment_appeal(
        db, org, actor, course_uuid, assignment_uuid, submission_uuid, str(appeal_uuid),
        correction=body,
        expected_decision_uuid=expected_decision_uuid,
        attempt_number=attempt_number,
    )


@router.post("/orgs/{org_uuid}/courses/{course_uuid}/assignments/{assignment_uuid}/submissions/{submission_uuid}/appeals/{appeal_uuid}/reopen")
async def api_reopen_assessment_appeal(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    assignment_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    submission_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    appeal_uuid: UUID,
    body: AssessmentAppealReopen,
    attempt_number: Annotated[int | None, Query(ge=1)] = None,
) -> AssessmentAppealReceipt:
    return await reopen_assessment_appeal(
        db, org, actor, course_uuid, assignment_uuid, submission_uuid, str(appeal_uuid), body,
        attempt_number=attempt_number,
    )

@router.get("/orgs/{org_uuid}/courses/{course_uuid}/configuration")
async def api_adaptive_configuration(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
) -> AdaptiveConfiguration:
    return await get_adaptive_configuration(request, db, org, actor, course_uuid)


AttemptUUID = Annotated[str, Path(pattern=r"^attempt_[0-9a-f-]{36}$")]
AttemptIdempotencyKey = Annotated[
    str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
]


@router.post("/orgs/{org_uuid}/me/diagnostic-attempts", status_code=201)
async def api_start_diagnostic(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    body: DiagnosticStart,
    idempotency_key: AttemptIdempotencyKey,
) -> AttemptRead:
    return await start_diagnostic(request, db, org, actor, body, idempotency_key)


@router.post("/orgs/{org_uuid}/me/practice-attempts", status_code=201)
async def api_start_practice(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    body: PracticeStart,
    idempotency_key: AttemptIdempotencyKey,
) -> AttemptRead:
    return await start_practice(request, db, org, actor, body, idempotency_key)


@router.get("/orgs/{org_uuid}/me/tutor-options")
async def api_read_tutor_options(
    request: Request,
    response: Response,
    org_uuid: str,
    actor: Annotated[PublicUser, Depends(get_authenticated_non_api_token_user)],
    db: AdaptiveDB,
    activity_uuid: Annotated[str, Query(min_length=1, max_length=89)],
    course_uuid: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    course_release_uuid: Annotated[
        str | None, Query(pattern=r"^course_release_[0-9a-f-]{36}$")
    ] = None,
) -> TutorOptions:
    org = await adaptive_organization(org_uuid, response, actor, db)
    if not await is_protected_studytomy_learner(db, actor.id):
        return TutorOptions(mode="legacy")
    await adaptive_session_actor(actor)
    return await read_tutor_options(
        request,
        db,
        org,
        actor,
        activity_uuid,
        course_uuid=course_uuid,
        course_release_uuid=course_release_uuid,
    )


@router.post("/orgs/{org_uuid}/me/tutor-sessions", status_code=201)
async def api_start_tutor_session(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    body: TutorStart,
) -> TutorSessionRead:
    return await start_tutor_session(request, db, org, actor, body)


@router.post("/orgs/{org_uuid}/me/tutor-sessions/{session_uuid}/close")
async def api_close_tutor_session(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    session_uuid: Annotated[str, Path(min_length=1, max_length=64)],
    body: TutorClose,
) -> TutorSessionRead:
    return await close_tutor_session(db, org, actor, session_uuid, body)


@router.post("/orgs/{org_uuid}/me/tutor-sessions/{session_uuid}/turns")
async def api_create_tutor_turn(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    session_uuid: Annotated[str, Path(min_length=1, max_length=64)],
    body: TutorTurnCreate,
) -> TutorTurnRead:
    return await create_tutor_turn(request, db, org, actor, session_uuid, body)


@router.get("/orgs/{org_uuid}/me/tutor-sessions/{session_uuid}/turns/{operation_uuid}")
async def api_read_tutor_turn(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    session_uuid: Annotated[str, Path(min_length=1, max_length=64)],
    operation_uuid: UUID,
) -> TutorTurnRead:
    return await read_tutor_turn(request, db, org, actor, session_uuid, operation_uuid)


@router.post("/orgs/{org_uuid}/me/tutor-sessions/{session_uuid}/feedback")
async def api_submit_tutor_feedback(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    session_uuid: Annotated[str, Path(min_length=1, max_length=64)],
    body: TutorFeedbackCreate,
) -> TutorFeedbackReceipt:
    return await submit_tutor_feedback(request, db, org, actor, session_uuid, body)


@router.get("/orgs/{org_uuid}/staff/learners/{learner_uuid}/courses/{course_uuid}/tutor-feedback")
async def api_list_tutor_feedback(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    learner_uuid: Annotated[str, Path(min_length=1, max_length=100)],
    course_uuid: Annotated[str, Path(min_length=1, max_length=100)],
    offset: Annotated[int, Query(ge=0, le=1000)] = 0,
) -> list[TutorFeedbackReviewRead]:
    return await review_tutor_feedback(db, org, actor, learner_uuid, course_uuid, offset=offset)


@router.post("/orgs/{org_uuid}/staff/learners/{learner_uuid}/courses/{course_uuid}/tutor-feedback")
async def api_review_tutor_feedback(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    learner_uuid: Annotated[str, Path(min_length=1, max_length=100)],
    course_uuid: Annotated[str, Path(min_length=1, max_length=100)],
    body: TutorFeedbackReviewCreate,
) -> TutorFeedbackReviewRead:
    reviews = await review_tutor_feedback(db, org, actor, learner_uuid, course_uuid, review=body)
    return reviews[0]


@router.get("/orgs/{org_uuid}/me/mastery")
async def api_read_learner_mastery(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    curriculum_revision_uuid: Annotated[
        str, Query(pattern=r"^curriculum_[0-9a-f-]{36}$")
    ],
    offset: Annotated[int, Query(ge=0, le=100)] = 0,
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
) -> MasteryPage:
    return await read_learner_mastery(
        request, db, org, actor, curriculum_revision_uuid, offset, limit
    )


@router.get("/orgs/{org_uuid}/staff/learners/{learner_uuid}/reports")
async def api_list_staff_reports(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    learner_uuid: Annotated[str, Path(min_length=1, max_length=100)],
    curriculum_revision_uuid: Annotated[
        str, Query(pattern=r"^curriculum_[0-9a-f-]{36}$")
    ],
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
) -> StaffReportList:
    return await list_staff_reports(
        request, db, org, actor, learner_uuid, curriculum_revision_uuid, cursor, limit
    )


@router.post("/orgs/{org_uuid}/staff/learners/{learner_uuid}/reports", status_code=201)
async def api_create_report_draft(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    learner_uuid: Annotated[str, Path(min_length=1, max_length=100)],
    body: ReportDraftCreate,
) -> ReportDraftRead:
    return await create_report_draft(request, db, org, actor, learner_uuid, body)


@router.get("/orgs/{org_uuid}/staff/reports/{report_uuid}")
async def api_read_staff_report_review(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    report_uuid: Annotated[str, Path(min_length=1, max_length=64)],
) -> StaffReportReview:
    return await read_staff_report_review(request, db, org, actor, report_uuid)


@router.put("/orgs/{org_uuid}/staff/reports/{report_uuid}")
async def api_edit_report(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    report_uuid: Annotated[str, Path(min_length=1, max_length=64)],
    body: ReportEdit,
) -> ReportDraftRead:
    return await review_report(request, db, org, actor, report_uuid, body)


@router.post("/orgs/{org_uuid}/staff/reports/{report_uuid}/approve")
async def api_approve_report(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    report_uuid: Annotated[str, Path(min_length=1, max_length=64)],
    body: ReportApproval,
) -> ReportDraftRead:
    return await review_report(request, db, org, actor, report_uuid, body)


@router.get("/orgs/{org_uuid}/staff/reports/{report_uuid}/recipients")
async def api_list_report_recipients(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    report_uuid: Annotated[str, Path(min_length=1, max_length=64)],
    expected_version: Annotated[int, Query(ge=1)],
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReportRecipientPage:
    return await list_report_recipients(
        request, db, org, actor, report_uuid, expected_version, offset
    )


@router.post("/orgs/{org_uuid}/staff/reports/{report_uuid}/recipient-preview")
async def api_preview_report_recipient(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    report_uuid: Annotated[str, Path(min_length=1, max_length=64)],
    body: ReportRecipientPreview,
) -> PublishedReportRead:
    return await preview_report_recipient(request, db, org, actor, report_uuid, body)


@router.post("/orgs/{org_uuid}/staff/reports/{report_uuid}/publish")
async def api_publish_report(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    report_uuid: Annotated[str, Path(min_length=1, max_length=64)],
    body: ReportPublish,
) -> ReportPublishReceipt:
    return await publish_report(request, db, org, actor, report_uuid, body)


@router.post("/orgs/{org_uuid}/staff/reports/{report_uuid}/notify")
async def api_notify_report(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    report_uuid: Annotated[str, Path(min_length=1, max_length=64)],
    expected_version: Annotated[int | None, Query(ge=1)] = None,
) -> ReportNotificationRead:
    return await request_report_notification(
        request, db, org, actor, report_uuid, expected_version
    )


@router.get(
    "/orgs/{org_uuid}/staff/report-deliveries/reconciliation",
    response_model=OutboxReconciliationPage,
)
async def api_list_report_delivery_reconciliation(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
    after: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> OutboxReconciliationPage:
    return await list_outbox_reconciliation(db, org, actor, limit=limit, after=after)


@router.post(
    "/orgs/{org_uuid}/staff/report-deliveries/{record_uuid}/reconciliation",
    response_model=OutboxReconciliationResult,
)
async def api_reconcile_report_delivery(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    record_uuid: Annotated[str, Path(min_length=1, max_length=100)],
    body: OutboxReconciliationDecision,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
    ],
) -> OutboxReconciliationResult:
    return await reconcile_outbox_delivery(
        db, org, actor, record_uuid, body, idempotency_key
    )


@router.get(
    "/orgs/{org_uuid}/staff/learners/{learner_uuid}/concepts/{concept_uuid}/mastery-reconciliation"
)
async def api_preview_mastery_reconciliation(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    learner_uuid: Annotated[str, Path(min_length=1, max_length=100)],
    concept_uuid: Annotated[str, Path(min_length=1, max_length=100)],
    policy_uuid: Annotated[str, Query(min_length=1, max_length=100)],
) -> dict:
    """Read-only integrity preview for staff assigned to the course and learner."""
    result = await reconcile_mastery(
        db, org, actor, learner_uuid, concept_uuid, policy_uuid
    )
    return jsonable_encoder(result)


@router.post(
    "/orgs/{org_uuid}/staff/learners/{learner_uuid}/concepts/{concept_uuid}/mastery-reconciliation/repair"
)
async def api_repair_mastery_projection(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    learner_uuid: Annotated[str, Path(min_length=1, max_length=100)],
    concept_uuid: Annotated[str, Path(min_length=1, max_length=100)],
    policy_uuid: Annotated[str, Query(min_length=1, max_length=100)],
    body: MasteryReconciliationRepair,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
    ],
) -> dict:
    """Repair only the projection shown by the caller's exact dry-run version."""
    operation = "studytomy.adaptive.mastery.repaired"
    await lock_organization(db, org.id)
    request_payload = {
        "learner_uuid": learner_uuid,
        "concept_uuid": concept_uuid,
        "policy_uuid": policy_uuid,
        **body.model_dump(mode="json"),
    }
    replay = await claim_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_payload=request_payload,
    )
    if replay is not None:
        if replay.response_projection is None:
            raise HTTPException(409, "The original repair response is unavailable.")
        await reconcile_mastery(db, org, actor, learner_uuid, concept_uuid, policy_uuid)
        return replay.response_projection

    result = await reconcile_mastery(
        db,
        org,
        actor,
        learner_uuid,
        concept_uuid,
        policy_uuid,
        repair=True,
        expected_evidence_watermark=body.expected_evidence_watermark,
        expected_projection_version=body.expected_projection_version,
    )
    projection = jsonable_encoder(result)
    await complete_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=operation,
        idempotency_key=idempotency_key,
        resource_kind="concept_mastery",
        resource_uuid=concept_uuid,
        response_status=200,
        response_projection=projection,
    )
    await db.commit()
    return projection


@router.post(
    "/orgs/{org_uuid}/staff/reports/{report_uuid}/withdraw",
    response_model=ReportWithdrawalReceipt,
)
async def api_withdraw_report(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    report_uuid: Annotated[str, Path(min_length=1, max_length=64)],
    body: ReportChange,
):
    return await change_published_report(
        request, db, org, actor, report_uuid, body, action="withdraw"
    )


@router.post(
    "/orgs/{org_uuid}/staff/reports/{report_uuid}/supersede",
    response_model=ReportDraftRead,
)
async def api_supersede_report(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    report_uuid: Annotated[str, Path(min_length=1, max_length=64)],
    body: ReportChange,
):
    return await change_published_report(
        request, db, org, actor, report_uuid, body, action="supersede"
    )


@router.get("/orgs/{org_uuid}/reports/{report_uuid}")
async def api_read_report(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    report_uuid: Annotated[str, Path(min_length=1, max_length=64)],
) -> PublishedReportRead | ReportDraftRead:
    return await read_report(request, db, org, actor, report_uuid)


@router.post("/orgs/{org_uuid}/reports/{report_uuid}/review-requests")
async def api_request_report_review(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    report_uuid: Annotated[str, Path(min_length=1, max_length=64)],
    body: ReportReviewRequest,
) -> ReportReviewRequestReceipt:
    return await request_report_review(db, org, actor, report_uuid, body)


@router.get("/orgs/{org_uuid}/me/reports")
async def api_list_my_reports(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
) -> MyReportsPage:
    return await list_my_reports(db, org, actor, cursor, limit)


@router.get("/orgs/{org_uuid}/staff/learners/{learner_uuid}/mastery")
async def api_read_staff_mastery(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    learner_uuid: Annotated[str, Path(min_length=1, max_length=100)],
    curriculum_revision_uuid: Annotated[
        str, Query(pattern=r"^curriculum_[0-9a-f-]{36}$")
    ],
    offset: Annotated[int, Query(ge=0, le=100)] = 0,
    limit: Annotated[int, Query(ge=1, le=20)] = 20,
) -> MasteryPage:
    return await read_staff_mastery(
        request, db, org, actor, learner_uuid, curriculum_revision_uuid, offset, limit
    )


@router.get("/orgs/{org_uuid}/me/lesson-mastery")
async def api_read_lesson_mastery(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    activity_uuid: Annotated[str, Query(min_length=1, max_length=89)],
    offset: Annotated[int, Query(ge=0, le=100)] = 0,
    course_uuid: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    course_release_uuid: Annotated[
        str | None, Query(pattern=r"^course_release_[0-9a-f-]{36}$")
    ] = None,
) -> MasteryPage:
    return await read_learner_mastery(
        request,
        db,
        org,
        actor,
        None,
        offset,
        20,
        activity_uuid=activity_uuid,
        course_uuid=course_uuid,
        course_release_uuid=course_release_uuid,
    )


@router.get("/orgs/{org_uuid}/me/recommendations")
async def api_read_learner_recommendations(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    activity_uuid: Annotated[str, Query(min_length=1, max_length=89)],
    course_uuid: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    course_release_uuid: Annotated[
        str | None, Query(pattern=r"^course_release_[0-9a-f-]{36}$")
    ] = None,
) -> list[Recommendation | NoRecommendation]:
    return await read_learner_recommendations(
        request,
        db,
        org,
        actor,
        activity_uuid,
        course_uuid=course_uuid,
        course_release_uuid=course_release_uuid,
    )


@router.get("/orgs/{org_uuid}/me/attempts/options")
async def api_lesson_attempt_options(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    source_activity_uuid: Annotated[str, Query(min_length=1, max_length=89)],
    course_uuid: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    course_release_uuid: Annotated[
        str | None, Query(pattern=r"^course_release_[0-9a-f-]{36}$")
    ] = None,
) -> list[LessonAttemptOption]:
    return await lesson_attempt_options(
        request,
        db,
        org,
        actor,
        source_activity_uuid,
        course_uuid=course_uuid,
        course_release_uuid=course_release_uuid,
    )


@router.get("/orgs/{org_uuid}/me/attempts/current")
async def api_read_current_attempt(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    source_activity_uuid: Annotated[str, Query(min_length=1, max_length=89)],
    purpose: Literal["diagnostic", "practice"],
    course_release_uuid: Annotated[
        str | None, Query(pattern=r"^course_release_[0-9a-f-]{36}$")
    ] = None,
) -> AttemptRead | None:
    return await read_current_attempt(
        request,
        db,
        org,
        actor,
        source_activity_uuid,
        purpose,
        course_release_uuid,
    )


@router.get("/orgs/{org_uuid}/me/attempts/{attempt_uuid}")
async def api_read_diagnostic_attempt(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    attempt_uuid: AttemptUUID,
) -> AttemptRead:
    return await read_diagnostic_attempt(request, db, org, actor, attempt_uuid)


@router.post("/orgs/{org_uuid}/me/attempts/{attempt_uuid}/responses")
async def api_respond_to_diagnostic(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    attempt_uuid: AttemptUUID,
    body: DiagnosticResponse,
    idempotency_key: AttemptIdempotencyKey,
) -> DiagnosticReceipt:
    return await respond_to_diagnostic(
        request, db, org, actor, attempt_uuid, body, idempotency_key
    )


@router.post("/orgs/{org_uuid}/me/attempts/{attempt_uuid}/abandon")
async def api_abandon_diagnostic(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    attempt_uuid: AttemptUUID,
    body: DiagnosticAbandon,
    idempotency_key: AttemptIdempotencyKey,
) -> AttemptRead:
    return await abandon_diagnostic(
        request, db, org, actor, attempt_uuid, body, idempotency_key
    )


@router.get("/orgs/{org_uuid}/me/attempts/{attempt_uuid}/summary")
async def api_read_diagnostic_summary(
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    attempt_uuid: AttemptUUID,
) -> DiagnosticSummary:
    return await read_diagnostic_summary(request, db, org, actor, attempt_uuid)


@router.get("/orgs/{org_uuid}/availability", response_model=AdaptiveAvailability)
async def api_get_adaptive_availability(
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
) -> AdaptiveAvailability:
    return await get_adaptive_availability(db, org, actor)


@router.post(
    "/orgs/{org_uuid}/curriculum-revisions",
    response_model=CurriculumDraftRead,
    status_code=201,
)
async def api_create_curriculum_draft(
    body: CurriculumDraftCreate,
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
    ],
) -> CurriculumDraftRead:
    return await create_curriculum_draft(request, db, org, actor, body, idempotency_key)


CurriculumUUID = Annotated[str, Path(pattern=r"^curriculum_[0-9a-f-]{36}$")]


@router.get("/orgs/{org_uuid}/courses/{course_uuid}/reviewed-curricula", response_model=ReviewedCurriculumPage)
async def api_reviewed_course_curricula(
    course_uuid: Annotated[str, Path(min_length=1, max_length=255)],
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    after: Annotated[str | None, Query(pattern=r"^curriculum_[0-9a-f-]{36}$")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> ReviewedCurriculumPage:
    return await list_reviewed_course_curricula(request, db, org, actor, course_uuid, after=after, limit=limit)


@router.get(
    "/orgs/{org_uuid}/curriculum-revisions/{revision_uuid}/objectives",
    response_model=ReviewedCurriculumObjectives,
)
async def api_reviewed_curriculum_objectives(
    revision_uuid: CurriculumUUID,
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
) -> ReviewedCurriculumObjectives:
    return await read_reviewed_curriculum_objectives(request, db, org, actor, revision_uuid)


@router.put(
    "/orgs/{org_uuid}/curriculum-revisions/{revision_uuid}",
    response_model=CurriculumDraftRead,
)
async def api_update_curriculum_draft(
    revision_uuid: CurriculumUUID,
    body: CurriculumDraftUpdate,
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
) -> CurriculumDraftRead:
    return await update_curriculum_draft(request, db, org, actor, revision_uuid, body)


@router.put(
    "/orgs/{org_uuid}/curriculum-revisions/{revision_uuid}/concepts",
    response_model=CurriculumGraphRead,
)
async def api_replace_curriculum_graph(
    revision_uuid: CurriculumUUID,
    body: CurriculumGraphUpdate,
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
) -> CurriculumGraphRead:
    return await replace_curriculum_graph(request, db, org, actor, revision_uuid, body)


@router.put(
    "/orgs/{org_uuid}/concepts/{concept_uuid}/activities",
    response_model=ConceptMappingRead,
)
async def api_replace_concept_mappings(
    concept_uuid: Annotated[str, Path(pattern=r"^concept_[0-9a-f-]{36}$")],
    body: ConceptMappingUpdate,
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
) -> ConceptMappingRead:
    return await replace_concept_mappings(request, db, org, actor, concept_uuid, body)


@router.get(
    "/orgs/{org_uuid}/curriculum-revisions/{revision_uuid}/review",
    response_model=CurriculumReviewSnapshot,
)
async def api_curriculum_review_snapshot(
    revision_uuid: CurriculumUUID,
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
) -> CurriculumReviewSnapshot:
    return await curriculum_review_snapshot(request, db, org, actor, revision_uuid)


@router.post(
    "/orgs/{org_uuid}/definitions/{kind}/{revision_uuid}/publish",
    response_model=CurriculumReviewedRead | ItemReviewedRead | DiagnosticStaffRead,
)
async def api_publish_definition(
    kind: Literal["curriculum", "item", "diagnostic"],
    revision_uuid: Annotated[
        str, Path(pattern=r"^(curriculum|item|diagnostic)_[0-9a-f-]{36}$")
    ],
    body: CurriculumPublish,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
    ],
) -> CurriculumReviewedRead | ItemReviewedRead | DiagnosticStaffRead:
    if not revision_uuid.startswith(f"{kind}_"):
        raise HTTPException(404, "Definition not found.")
    if kind == "item":
        return await review_item(db, org, actor, revision_uuid, body, idempotency_key)
    if kind == "diagnostic":
        return await review_diagnostic(
            db, org, actor, revision_uuid, body, idempotency_key
        )
    return await review_curriculum(db, org, actor, revision_uuid, body, idempotency_key)


@router.post(
    "/orgs/{org_uuid}/definitions/{kind}/{revision_uuid}/retire",
    response_model=CurriculumReviewedRead | ItemReviewedRead | DiagnosticStaffRead,
)
async def api_retire_definition(
    kind: Literal["curriculum", "item", "diagnostic"],
    revision_uuid: Annotated[
        str, Path(pattern=r"^(curriculum|item|diagnostic)_[0-9a-f-]{36}$")
    ],
    body: CurriculumRetire,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
    ],
) -> CurriculumReviewedRead | ItemReviewedRead | DiagnosticStaffRead:
    if not revision_uuid.startswith(f"{kind}_"):
        raise HTTPException(404, "Definition not found.")
    if kind == "item":
        return await review_item(db, org, actor, revision_uuid, body, idempotency_key)
    if kind == "diagnostic":
        return await review_diagnostic(
            db, org, actor, revision_uuid, body, idempotency_key
        )
    return await review_curriculum(db, org, actor, revision_uuid, body, idempotency_key)


@router.get(
    "/orgs/{org_uuid}/item-sources/{task_uuid}",
    response_model=NativeItemSourceRead,
)
async def api_item_source_preview(
    task_uuid: Annotated[str, Path(min_length=1, max_length=100)],
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    question_uuid: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
) -> NativeItemSourceRead:
    source, _private_answer = await read_native_item_source(
        request,
        db,
        org,
        actor,
        task_uuid,
        question_uuid,
    )
    return source


@router.post(
    "/orgs/{org_uuid}/item-revisions",
    status_code=201,
    response_model=ItemRevisionStaffRead,
)
async def api_create_item_revision(
    body: ItemRevisionCreate | OriginalItemRevisionCreate,
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
    ],
) -> ItemRevisionStaffRead:
    return await create_item_revision(request, db, org, actor, body, idempotency_key)


@router.put(
    "/orgs/{org_uuid}/item-revisions/{item_uuid}",
    response_model=ItemRevisionStaffRead,
)
async def api_update_item_revision(
    item_uuid: Annotated[str, Path(pattern=r"^item_[0-9a-f-]{36}$")],
    body: ItemRevisionUpdate,
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
) -> ItemRevisionStaffRead:
    return await update_item_revision(request, db, org, actor, item_uuid, body)


@router.get(
    "/orgs/{org_uuid}/item-revisions/{item_uuid}/review",
    response_model=ItemReviewSnapshot,
)
async def api_item_review_snapshot(
    item_uuid: Annotated[str, Path(pattern=r"^item_[0-9a-f-]{36}$")],
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
) -> ItemReviewSnapshot:
    return await item_review_snapshot(request, db, org, actor, item_uuid)


@router.get(
    "/orgs/{org_uuid}/item-revisions/{item_uuid}/grading-review",
    response_model=ItemGradingReviewSnapshot,
)
async def api_item_grading_review_snapshot(
    item_uuid: Annotated[str, Path(pattern=r"^item_[0-9a-f-]{36}$")],
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
) -> ItemGradingReviewSnapshot:
    return await item_grading_review_snapshot(db, org, actor, item_uuid)


@router.post(
    "/orgs/{org_uuid}/diagnostic-revisions",
    status_code=201,
    response_model=DiagnosticStaffRead,
)
async def api_create_diagnostic(
    body: DiagnosticDraftCreate,
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=255)
    ],
) -> DiagnosticStaffRead:
    return await create_diagnostic(request, db, org, actor, body, idempotency_key)


@router.get(
    "/orgs/{org_uuid}/diagnostic-revisions/{revision_uuid}",
    response_model=DiagnosticStaffRead,
)
async def api_read_diagnostic(
    revision_uuid: Annotated[str, Path(pattern=r"^diagnostic_[0-9a-f-]{36}$")],
    request: Request,
    org: AdaptiveOrg,
    actor: AdaptiveActor,
    db: AdaptiveDB,
) -> DiagnosticStaffRead:
    return await read_diagnostic(request, db, org, actor, revision_uuid)


@router.get("/orgs/{org_uuid}/me/topic-check")
async def api_read_topic_check(
    org: AdaptiveOrg, actor: AdaptiveActor, db: AdaptiveDB, scope: Annotated[TopicScope, Query()],
) -> TopicCheckView:
    return await read_topic_check(db, org, actor, scope)


@router.post("/orgs/{org_uuid}/me/topic-check")
async def api_submit_topic_check(
    org: AdaptiveOrg, actor: AdaptiveActor, db: AdaptiveDB, body: TopicSubmission,
) -> TopicCheckView:
    return await submit_topic_check(db, org, actor, body)


@router.get("/orgs/{org_uuid}/staff/learners/{learner_uuid}/topic-check")
async def api_read_topic_timeline(
    request: Request, org: AdaptiveOrg, actor: AdaptiveActor, db: AdaptiveDB,
    learner_uuid: Annotated[str, Path(min_length=1, max_length=100)], scope: Annotated[TopicScope, Query()],
) -> TopicTimeline:
    return await read_topic_timeline(request, db, org, actor, learner_uuid, scope)
