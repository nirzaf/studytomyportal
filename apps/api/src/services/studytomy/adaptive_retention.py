"""Deletion boundaries for records awaiting approved retention handling."""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.courses.certifications import CertificateUser, Certifications
from src.db.courses.courses import Course
from src.db.studytomy.adaptive import StudyCurriculumRevision
from src.db.studytomy.adaptive_diagnostics import StudyDiagnosticRevision
from src.db.studytomy.adaptive_evidence import (
    StudyLearningAttempt,
    StudyLearningEvidence,
)
from src.db.studytomy.adaptive_items import StudyItemRevision
from src.db.studytomy.adaptive_reports import (
    StudyProgressReport,
    StudyReportRecipient,
    StudyReportReviewRequest,
)
from src.db.studytomy.education import StudyCourseReview
from src.db.trail_runs import TrailRun
from src.db.trail_steps import TrailStep
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.policies import lock_organization


def _protected_record():
    return studytomy_http_error(
        409,
        "ADAPTIVE_RETENTION_REVIEW_REQUIRED",
        "Adaptive learning records reference this resource. Request a retention review before deleting it.",
    )


async def _ensure_no_course_review(db: AsyncSession, *scope) -> None:
    result = await db.execute(select(StudyCourseReview.id).where(*scope).limit(1))
    if result.scalar_one_or_none() is not None:
        raise studytomy_http_error(
            409,
            "COURSE_REVIEW_RETENTION_REQUIRED",
            "Stored course reviews reference this resource. Request a retention review before deleting it.",
        )


async def ensure_adaptive_org_deletable(db: AsyncSession, org_id: int) -> None:
    # All adaptive evidence, report and tutor trees reference a curriculum.
    result = await db.execute(
        select(StudyCurriculumRevision.id)
        .where(StudyCurriculumRevision.org_id == org_id)
        .limit(1)
    )
    if result.scalar_one_or_none() is not None:
        raise _protected_record()
    await _ensure_no_course_review(db, StudyCourseReview.org_id == org_id)


async def ensure_adaptive_course_deletable(
    db: AsyncSession, org_id: int, course_uuid: str
) -> None:
    await lock_organization(db, org_id)
    report = await db.execute(
        select(StudyProgressReport.id)
        .where(
            StudyProgressReport.org_id == org_id,
            StudyProgressReport.owner_course_uuid == course_uuid,
        )
        .limit(1)
    )
    if report.scalar_one_or_none() is not None:
        raise _protected_record()
    await _ensure_no_course_review(
        db, StudyCourseReview.org_id == org_id, StudyCourseReview.course_uuid == course_uuid
    )


async def ensure_managed_progress_source_deletable(
    db: AsyncSession, org_id: int, course_id: int, *, activity_ids: list[int] | None = None,
) -> None:
    """Protect native progress from foreign-key cascades on content deletion."""
    managed = (await db.execute(select(Course.studytomy_managed).where(
        Course.org_id == org_id, Course.id == course_id,
    ).with_for_update())).scalar_one_or_none()
    if not managed:
        return
    steps = select(TrailStep.id).where(TrailStep.course_id == course_id)
    if activity_ids is None:
        runs = select(TrailRun.id).where(TrailRun.course_id == course_id)
        retained = or_(steps.exists(), runs.exists())
    else:
        retained = steps.where(TrailStep.activity_id.in_(activity_ids)).exists()
    if (await db.execute(select(retained))).scalar_one():
        raise studytomy_http_error(
            409, "MANAGED_PROGRESS_REVIEW_REQUIRED", "Retained course progress requires a reviewed correction.",
        )
    if activity_ids is None:
        awards = select(CertificateUser.id).join(Certifications, Certifications.id == CertificateUser.certification_id).where(
            Certifications.course_id == course_id,
        ).exists()
        if (await db.execute(select(awards))).scalar_one():
            raise studytomy_http_error(409, "MANAGED_AWARD_REVIEW_REQUIRED", "This course has retained awards that require review.")


async def ensure_adaptive_user_deletable(db: AsyncSession, user_id: int) -> None:
    report = StudyProgressReport
    references = (
        (StudyCurriculumRevision.id, StudyCurriculumRevision.reviewer_id == user_id),
        (StudyItemRevision.id, StudyItemRevision.reviewer_id == user_id),
        (StudyDiagnosticRevision.id, StudyDiagnosticRevision.reviewer_id == user_id),
        (StudyLearningAttempt.id, StudyLearningAttempt.actor_user_id == user_id),
        (StudyLearningEvidence.id, StudyLearningEvidence.actor_user_id == user_id),
        (
            report.id,
            or_(
                report.requester_user_id == user_id,
                report.reviewer_user_id == user_id,
                report.changed_by_user_id == user_id,
            ),
        ),
        (StudyReportRecipient.id, StudyReportRecipient.recipient_user_id == user_id),
        (
            StudyReportReviewRequest.id,
            StudyReportReviewRequest.requester_user_id == user_id,
        ),
    )
    for row_id, reference in references:
        if (
            await db.execute(select(row_id).where(reference).limit(1))
        ).scalar_one_or_none() is not None:
            raise _protected_record()
    await _ensure_no_course_review(
        db, or_(StudyCourseReview.owner_user_id == user_id, StudyCourseReview.reviewer_user_id == user_id)
    )
