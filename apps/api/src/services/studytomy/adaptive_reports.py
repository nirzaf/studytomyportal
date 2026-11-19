"""Freeze an authorized, single-learner progress report draft."""

from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.orm import aliased
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.adaptive import StudyConcept, StudyCurriculumRevision
from src.db.studytomy.adaptive_evidence import (
    StudyAttemptItem,
    StudyLearningAttempt,
    StudyLearningEvidence,
)
from src.db.studytomy.adaptive_reports import (
    StudyProgressReport,
    StudyReportRecipient,
    StudyReportReviewRequest,
)
from src.db.studytomy.family import (
    StudyLearnerControlMode,
    StudyLearnerProfile,
    StudyRelationship,
    StudyRelationshipStatus,
    StudyRelationshipType,
    StudySharingGrant,
)
from src.db.studytomy.governance import StudyPolicyKey
from src.db.studytomy.operations import StudyOutboxRecord
from src.db.studytomy.privacy import StudyConsentPurpose
from src.db.trail_steps import TrailStep
from src.db.users import PublicUser, User
from src.security.rbac import AccessAction, AccessContext, check_resource_access
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_studytomy_staff_action,
)
from src.services.studytomy.adaptive_access import (
    _is_learning_eligible,
    adaptive_enabled,
)
from src.services.studytomy.adaptive_curriculum_review import _REVIEW_POLICIES
from src.services.studytomy.adaptive_evidence import _watermark
from src.services.studytomy.adaptive_progress import (
    _published_or_managed_curriculum,
    _read_policy,
)
from src.services.studytomy.authority import has_active_guardian_scope
from src.services.studytomy.consents import has_current_consent_scope
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import _as_utc, lock_organization
from src.services.studytomy.record_delivery import reviewed_completion_evidence_in_period
from src.services.studytomy.sharing import current_independent_sharing_grant


class ReportDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_uuid: UUID
    curriculum_revision_uuid: Annotated[
        str, Field(pattern=r"^curriculum_[0-9a-f-]{36}$")
    ]
    period_from: AwareDatetime
    period_to: AwareDatetime


class ConceptObservation(BaseModel):
    concept_uuid: str
    title: str
    independent_correct: int = 0
    independent_incorrect: int = 0
    supported_practice: int = 0
    unknown: int = 0
    pending: int = 0
    missing_independent_evidence: bool = True


class ReportSummary(BaseModel):
    native_completions: int
    reviewed_release_completions: int | None = Field(default=None, exclude_if=lambda value: value is None)
    completion_basis: Literal["current_completion_last_updated_in_period"] = (
        "current_completion_last_updated_in_period"
    )
    concepts: list[ConceptObservation]


class ReportDraftRead(BaseModel):
    uuid: str
    status: Literal["draft", "approved", "published", "withdrawn"]
    commentary: str | None
    approved_at: AwareDatetime | None
    version: int
    curriculum_revision_uuid: str
    period_from: AwareDatetime
    period_to: AwareDatetime
    evidence_watermark: int
    digest: str
    summary: ReportSummary


def _report_read(row: StudyProgressReport, revision_uuid: str) -> ReportDraftRead:
    return ReportDraftRead(
        uuid=row.uuid,
        status=row.status,
        commentary=row.commentary,
        approved_at=_as_utc(row.approved_at) if row.approved_at else None,
        version=row.version,
        curriculum_revision_uuid=revision_uuid,
        period_from=_as_utc(row.period_from),
        period_to=_as_utc(row.period_to),
        evidence_watermark=row.evidence_watermark,
        digest=row.digest,
        summary=ReportSummary.model_validate(row.summary),
    )


async def _snapshot(
    db: AsyncSession,
    org_id: int,
    profile: StudyLearnerProfile,
    curriculum_id: int,
    course_id: int,
    course_uuid: str,
    start: datetime,
    end: datetime,
    watermark: int,
    now: datetime,
) -> tuple[ReportSummary, dict]:
    concepts = (
        await db.execute(
            select(StudyConcept)
            .where(
                StudyConcept.org_id == org_id,
                StudyConcept.curriculum_revision_id == curriculum_id,
                StudyConcept.archived.is_(False),
            )
            .order_by(StudyConcept.sequence, StudyConcept.id)
        )
    ).scalars()
    counts = {
        concept.id: ConceptObservation(concept_uuid=concept.uuid, title=concept.title)
        for concept in concepts
    }
    event = StudyLearningEvidence
    journal = (
        select(
            event.id,
            func.row_number()
            .over(
                partition_by=event.logical_source_uuid,
                order_by=event.result_revision.desc(),
            )
            .label("rank"),
            func.min(event.observed_at)
            .over(partition_by=event.logical_source_uuid)
            .label("first_observed"),
        )
        .where(
            event.org_id == org_id,
            event.learner_profile_id == profile.id,
            event.curriculum_revision_id == curriculum_id,
            event.evidence_sequence <= watermark,
            event.concept_id.in_(counts),
        )
        .subquery()
    )
    evidence = (
        await db.execute(
            select(event, StudyLearningAttempt.kind)
            .join(journal, journal.c.id == event.id)
            .join(StudyAttemptItem, StudyAttemptItem.id == event.attempt_item_id)
            .join(
                StudyLearningAttempt,
                StudyLearningAttempt.id == StudyAttemptItem.attempt_id,
            )
            .where(
                journal.c.rank == 1,
                journal.c.first_observed >= start,
                journal.c.first_observed < end,
                StudyLearningAttempt.source_course_uuid == course_uuid,
            )
            .order_by(event.evidence_sequence)
        )
    ).all()
    refs = []
    for row, kind in evidence:
        refs.append(
            {
                "uuid": row.uuid,
                "revision": row.result_revision,
                "sequence": row.evidence_sequence,
                "policy_version_ids": row.policy_version_ids,
            }
        )
        if row.is_retraction:
            continue
        concept = counts[row.concept_id]
        if (
            row.eligible
            and row.independence == "independent"
            and row.outcome in ("correct", "incorrect")
        ):
            concept.missing_independent_evidence = False
            if row.outcome == "correct":
                concept.independent_correct += 1
            else:
                concept.independent_incorrect += 1
        elif kind == "practice":
            concept.supported_practice += 1
        else:
            concept.unknown += 1
    pending = (
        await db.execute(
            select(StudyAttemptItem.uuid, StudyAttemptItem.concept_id)
            .join(
                StudyLearningAttempt,
                StudyLearningAttempt.id == StudyAttemptItem.attempt_id,
            )
            .where(
                StudyAttemptItem.org_id == org_id,
                StudyAttemptItem.curriculum_revision_id == curriculum_id,
                StudyAttemptItem.learner_profile_id == profile.id,
                StudyAttemptItem.concept_id.in_(counts),
                StudyAttemptItem.response_state == "issued",
                StudyAttemptItem.issued_at >= start,
                StudyAttemptItem.issued_at < end,
                StudyLearningAttempt.status == "active",
                StudyLearningAttempt.source_course_uuid == course_uuid,
                StudyLearningAttempt.expires_at > now,
            )
            .order_by(StudyAttemptItem.uuid)
        )
    ).all()
    for _, concept_id in pending:
        counts[concept_id].pending += 1
    steps = (
        await db.execute(
            select(TrailStep.id, TrailStep.activity_id, TrailStep.update_date)
            .where(
                TrailStep.org_id == org_id,
                TrailStep.course_id == course_id,
                TrailStep.user_id == profile.user_id,
                TrailStep.complete.is_(True),
            )
            .order_by(TrailStep.id)
        )
    ).all()
    completion = []
    for step_id, activity_id, updated in steps:
        try:
            timestamp = _as_utc(datetime.fromisoformat(updated))
        except ValueError:
            continue
        if start <= timestamp < end:
            completion.append(
                {"id": step_id, "activity_id": activity_id, "updated": updated}
            )
    release_completions = await reviewed_completion_evidence_in_period(
        db, org_id, profile.user_id, course_uuid, start, end,
    )
    return ReportSummary(
        native_completions=len({row["activity_id"] for row in completion}),
        reviewed_release_completions=len({row["release_uuid"] for row in release_completions}) or None,
        concepts=list(counts.values()),
    ), {
        "evidence": refs,
        "pending_items": [uuid for uuid, _ in pending],
        "native_completion": completion,
        **({"reviewed_release_completion": release_completions} if release_completions else {}),
    }


async def _report_snapshot_is_current(
    db: AsyncSession,
    row: StudyProgressReport,
    profile: StudyLearnerProfile,
    curriculum_id: int,
    course_id: int,
    course_uuid: str,
    now: datetime,
) -> bool:
    watermark = await _watermark(db, row.org_id, profile.id)
    summary, sources = await _snapshot(
        db,
        row.org_id,
        profile,
        curriculum_id,
        course_id,
        course_uuid,
        _as_utc(row.period_from),
        _as_utc(row.period_to),
        watermark,
        now,
    )
    return sources == row.sources and summary.model_dump(mode="json") == row.summary


async def _report_authority(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    learner_uuid: str,
    revision_uuid: str,
    now: datetime,
):
    unavailable = HTTPException(404, "Learning record not found.")
    if not await adaptive_enabled(db, org):
        raise unavailable
    profile = (
        await db.execute(
            select(StudyLearnerProfile)
            .where(
                StudyLearnerProfile.org_id == org.id,
                StudyLearnerProfile.learner_uuid == learner_uuid,
                StudyLearnerProfile.status == "active",
                StudyLearnerProfile.control_mode.in_(("independent", "dependent")),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if profile is None:
        raise unavailable
    try:
        curriculum, course = await _published_or_managed_curriculum(
            db, org, profile, revision_uuid, now
        )
        for scope, uuid in (("learner", learner_uuid), ("course", course.course_uuid)):
            await authorize_studytomy_staff_action(
                db,
                actor,
                org,
                StudytomyCapabilityName.PROGRESS_REPORT_REVIEW,
                target_scope_kind=scope,
                target_resource_uuid=uuid,
                subject_user_id=profile.user_id,
                hide_existence_on_denial=True,
                now=now,
            )
    except HTTPException as error:
        if error.status_code not in (403, 404):
            raise
        raise unavailable from error
    native = await check_resource_access(
        request,
        db,
        actor,
        course.course_uuid,
        AccessAction.UPDATE,
        context=AccessContext.DASHBOARD,
        raise_on_deny=False,
    )
    if (
        not native.allowed
        or not await _is_learning_eligible(db, org, profile, now)
        or not await has_current_consent_scope(
            db,
            org,
            profile,
            StudyConsentPurpose.LEARNING_TERMS,
            "adaptive_progress_report",
            now,
        )
    ):
        raise unavailable
    policy_ids = await _report_policies(db, org, curriculum, now)
    return profile, curriculum, course, policy_ids


async def _report_policies(
    db: AsyncSession,
    org: Organization,
    curriculum: StudyCurriculumRevision,
    now: datetime,
) -> list[int]:
    policies = await _current_policies(
        db,
        org,
        (
            *_REVIEW_POLICIES,
            StudyPolicyKey.D_01,
            StudyPolicyKey.D_03,
            StudyPolicyKey.D_05,
            StudyPolicyKey.D_08,
            StudyPolicyKey.D_12,
            StudyPolicyKey.D_13,
            StudyPolicyKey.D_18,
        ),
        now,
    )
    await _read_policy(db, org, curriculum, now)
    return sorted(int(policy.id) for policy in policies)


async def create_report_draft(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    learner_uuid: str,
    body: ReportDraftCreate,
) -> ReportDraftRead:
    start, end = _as_utc(body.period_from), _as_utc(body.period_to)
    now = datetime.now(UTC)
    if not start < end <= now or end - start > timedelta(days=366):
        raise HTTPException(
            422, "Select a completed reporting period of up to 366 days."
        )
    await lock_organization(db, org.id)
    profile, curriculum, course, policy_ids = await _report_authority(
        request,
        db,
        org,
        actor,
        learner_uuid,
        body.curriculum_revision_uuid,
        now,
    )
    existing = (
        await db.execute(
            select(StudyProgressReport).where(
                StudyProgressReport.org_id == org.id,
                StudyProgressReport.requester_user_id == actor.id,
                StudyProgressReport.operation_uuid == str(body.operation_uuid),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if (
            existing.learner_profile_id,
            existing.curriculum_revision_id,
            _as_utc(existing.period_from),
            _as_utc(existing.period_to),
        ) != (
            profile.id,
            curriculum.id,
            start,
            end,
        ):
            raise studytomy_http_error(
                409,
                "IDEMPOTENCY_CONFLICT",
                "This operation belongs to a different report.",
            )
        if existing.policy_version_ids != policy_ids:
            raise studytomy_http_error(
                409, "STALE_VERSION", "Create a draft under the current policy."
            )
        return _report_read(existing, curriculum.uuid)
    watermark = await _watermark(db, org.id, profile.id)
    summary, sources = await _snapshot(
        db,
        org.id,
        profile,
        curriculum.id,
        course.id,
        course.course_uuid,
        start,
        end,
        watermark,
        now,
    )
    public_summary = summary.model_dump(mode="json")
    draft = StudyProgressReport(
        org_id=org.id,
        learner_profile_id=profile.id,
        curriculum_revision_id=curriculum.id,
        owner_course_uuid=course.course_uuid,
        requester_user_id=actor.id,
        operation_uuid=str(body.operation_uuid),
        period_from=start,
        period_to=end,
        evidence_watermark=watermark,
        policy_version_ids=policy_ids,
        summary=public_summary,
        sources=sources,
        digest=canonical_request_hash(
            {
                "schema": "adaptive_progress_report_v1",
                "learner": learner_uuid,
                "curriculum": curriculum.uuid,
                "course": course.course_uuid,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "watermark": watermark,
                "policies": policy_ids,
                "summary": public_summary,
                "sources": sources,
            }
        ),
    )
    db.add(draft)
    await db.flush()
    response = _report_read(draft, curriculum.uuid)
    await db.commit()
    return response


class ReportEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Annotated[int, Field(strict=True, ge=1)]
    commentary: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                min_length=1,
                max_length=600,
                pattern=r"^[^<>\x00-\x1f\x7f]+$",
            ),
        ]
        | None
    )


class ReportApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Annotated[int, Field(strict=True, ge=1)]
    expected_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


async def _latest_report(db: AsyncSession, org: Organization, report_uuid: str):
    latest = (
        await db.execute(
            select(
                StudyProgressReport,
                StudyLearnerProfile.learner_uuid,
                StudyCurriculumRevision.uuid,
            )
            .join(
                StudyLearnerProfile,
                StudyLearnerProfile.id == StudyProgressReport.learner_profile_id,
            )
            .join(
                StudyCurriculumRevision,
                StudyCurriculumRevision.id
                == StudyProgressReport.curriculum_revision_id,
            )
            .where(
                StudyProgressReport.org_id == org.id,
                StudyProgressReport.uuid == report_uuid,
                StudyLearnerProfile.org_id == org.id,
                StudyCurriculumRevision.org_id == org.id,
            )
            .order_by(StudyProgressReport.version.desc())
            .limit(1)
        )
    ).one_or_none()
    if latest is None:
        raise HTTPException(404, "Learning record not found.")
    return latest


async def review_report(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    report_uuid: str,
    body: ReportEdit | ReportApproval,
) -> ReportDraftRead:
    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    row, learner_uuid, revision_uuid = await _latest_report(db, org, report_uuid)
    profile, curriculum, course, policy_ids = await _report_authority(
        request,
        db,
        org,
        actor,
        learner_uuid,
        revision_uuid,
        now,
    )
    if (
        row.status in ("published", "withdrawn")
        or row.version != body.expected_version
        or row.policy_version_ids != policy_ids
        or (
            isinstance(body, ReportApproval)
            and (row.status != "draft" or row.digest != body.expected_digest)
        )
    ):
        raise studytomy_http_error(
            409, "STALE_VERSION", "Reload the current report draft."
        )
    start, end = _as_utc(row.period_from), _as_utc(row.period_to)
    approve = isinstance(body, ReportApproval)
    if approve and not await _report_snapshot_is_current(
        db,
        row,
        profile,
        curriculum.id,
        course.id,
        course.course_uuid,
        now,
    ):
        raise studytomy_http_error(
            409, "STALE_VERSION", "Report evidence changed. Create a fresh draft."
        )
    commentary = row.commentary if approve else body.commentary
    digest = (
        row.digest
        if approve
        else canonical_request_hash(
            {
                "schema": "adaptive_progress_report_review_v1",
                "previous_digest": row.digest,
                "commentary": commentary,
            }
        )
    )
    version = StudyProgressReport(
        uuid=row.uuid,
        org_id=org.id,
        learner_profile_id=profile.id,
        curriculum_revision_id=curriculum.id,
        owner_course_uuid=course.course_uuid,
        requester_user_id=row.requester_user_id,
        period_from=start,
        period_to=end,
        evidence_watermark=row.evidence_watermark,
        policy_version_ids=policy_ids,
        summary=row.summary,
        sources=row.sources,
        digest=digest,
        commentary=commentary,
        version=row.version + 1,
        status="approved" if approve else "draft",
        reviewer_user_id=actor.id if approve else None,
        approved_at=now if approve else None,
    )
    db.add(version)
    await db.flush()
    read = _report_read(version, revision_uuid)
    await db.commit()
    return read


_REPORT_PROJECTION = "adaptive_report_summary"


class ReportPublish(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Annotated[int, Field(strict=True, ge=1)]
    recipient_grant_uuids: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=100)]],
        Field(default_factory=list, max_length=20),
    ]
    guardian_relationship_uuids: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=100)]],
        Field(default_factory=list, max_length=20),
    ]


class ReportPublishReceipt(BaseModel):
    uuid: str
    version: int
    digest: str
    status: Literal["published"] = "published"
    recipient_count: int


async def _authorize_current_approved_report(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    report_uuid: str,
    expected_version: int,
):
    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    row, learner_uuid, revision_uuid = await _latest_report(db, org, report_uuid)
    profile, curriculum, course, policy_ids = await _report_authority(
        request,
        db,
        org,
        actor,
        learner_uuid,
        revision_uuid,
        now,
    )
    if (
        row.status != "approved"
        or row.version != expected_version
        or row.policy_version_ids != policy_ids
    ):
        raise studytomy_http_error(
            409, "STALE_VERSION", "Reload the current approved report."
        )
    if not await _report_snapshot_is_current(
        db,
        row,
        profile,
        curriculum.id,
        course.id,
        course.course_uuid,
        now,
    ):
        raise studytomy_http_error(
            409, "STALE_VERSION", "Report evidence changed. Create a fresh draft."
        )
    return row, profile, curriculum, course, policy_ids, now


async def _authorize_report_release(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    report_uuid: str,
    body: ReportPublish,
):
    if not body.recipient_grant_uuids and not body.guardian_relationship_uuids:
        raise HTTPException(422, "Select at least one authorized recipient.")
    if len(set(body.recipient_grant_uuids)) != len(body.recipient_grant_uuids):
        raise HTTPException(422, "Select each sharing grant once.")
    if len(set(body.guardian_relationship_uuids)) != len(body.guardian_relationship_uuids):
        raise HTTPException(422, "Select each guardian relationship once.")
    (
        row,
        profile,
        curriculum,
        course,
        policy_ids,
        now,
    ) = await _authorize_current_approved_report(
        request, db, org, actor, report_uuid, body.expected_version
    )
    unavailable = studytomy_http_error(
        409,
        "REPORT_RECIPIENT_NOT_AUTHORIZED",
        "One or more selected recipients lack current report authority.",
    )
    if profile.control_mode == StudyLearnerControlMode.DEPENDENT.value:
        if body.recipient_grant_uuids or not body.guardian_relationship_uuids:
            raise unavailable
        relationships = (
            (
                await db.execute(
                    select(StudyRelationship)
                    .where(
                        StudyRelationship.org_id == org.id,
                        StudyRelationship.learner_profile_id == profile.id,
                        StudyRelationship.relationship_type
                        == StudyRelationshipType.GUARDIAN.value,
                        StudyRelationship.status
                        == StudyRelationshipStatus.ACCEPTED.value,
                        StudyRelationship.relationship_uuid.in_(
                            body.guardian_relationship_uuids
                        ),
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        if len(relationships) != len(body.guardian_relationship_uuids):
            raise unavailable
        for rel in relationships:
            if not await has_active_guardian_scope(
                db,
                org,
                profile,
                int(rel.adult_user_id),
                _REPORT_PROJECTION,
                now=now,
                relationship_binding=(rel.relationship_uuid, rel.authority_version),
            ):
                raise unavailable
        return row, profile, curriculum, course, policy_ids, [], relationships, now

    if body.guardian_relationship_uuids or not body.recipient_grant_uuids:
        raise unavailable
    grants = (
        (
            await db.execute(
                select(StudySharingGrant)
                .where(
                    StudySharingGrant.org_id == org.id,
                    StudySharingGrant.learner_profile_id == profile.id,
                    StudySharingGrant.grant_uuid.in_(body.recipient_grant_uuids),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    if len(grants) != len(body.recipient_grant_uuids):
        raise unavailable
    for grant in grants:
        current = await current_independent_sharing_grant(
            db,
            org,
            profile,
            grant.recipient_user_id,
            _REPORT_PROJECTION,
            now=now,
        )
        if current is None or current.id != grant.id:
            raise unavailable
    return row, profile, curriculum, course, policy_ids, grants, [], now


async def publish_report(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    report_uuid: str,
    body: ReportPublish,
) -> ReportPublishReceipt:
    (
        row,
        profile,
        curriculum,
        course,
        policy_ids,
        grants,
        relationships,
        now,
    ) = await _authorize_report_release(request, db, org, actor, report_uuid, body)
    start, end = _as_utc(row.period_from), _as_utc(row.period_to)
    published = StudyProgressReport(
        uuid=row.uuid,
        org_id=org.id,
        learner_profile_id=profile.id,
        curriculum_revision_id=curriculum.id,
        owner_course_uuid=course.course_uuid,
        requester_user_id=row.requester_user_id,
        period_from=start,
        period_to=end,
        evidence_watermark=row.evidence_watermark,
        policy_version_ids=policy_ids,
        summary=row.summary,
        sources=row.sources,
        digest=row.digest,
        commentary=row.commentary,
        version=row.version + 1,
        status="published",
        reviewer_user_id=row.reviewer_user_id,
        approved_at=row.approved_at,
    )
    db.add(published)
    await db.flush()
    for grant in grants:
        db.add(
            StudyReportRecipient(
                org_id=org.id,
                report_id=published.id,
                recipient_user_id=grant.recipient_user_id,
                recipient_capacity="independent_sharing",
                sharing_grant_id=grant.id,
                relationship_id=None,
                grant_version=grant.authority_version,
                projection=_REPORT_PROJECTION,
                available_at=now,
            )
        )
    for rel in relationships:
        db.add(
            StudyReportRecipient(
                org_id=org.id,
                report_id=published.id,
                recipient_user_id=rel.adult_user_id,
                recipient_capacity="guardian_authority",
                sharing_grant_id=None,
                relationship_id=rel.id,
                grant_version=rel.authority_version,
                projection=_REPORT_PROJECTION,
                available_at=now,
            )
        )
    receipt = ReportPublishReceipt(
        uuid=published.uuid,
        version=published.version,
        digest=published.digest,
        recipient_count=len(grants) + len(relationships),
    )
    await db.commit()
    return receipt


class ReportMetadata(BaseModel):
    uuid: str
    version: int
    period_from: AwareDatetime
    period_to: AwareDatetime
    curriculum_title: str
    course_title: str


class PublishedReportRead(ReportMetadata):
    learner_name: str
    can_request_review: bool
    evidence_cutoff: AwareDatetime
    summary: ReportSummary
    commentary: str | None


class MyReportsPage(BaseModel):
    items: list[ReportMetadata]
    next_cursor: str | None


async def _published_report_context(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    row: StudyProgressReport,
    revision_uuid: str,
    now: datetime,
    *,
    staff: bool = False,
):
    if row.status != "published" or not await adaptive_enabled(db, org):
        return None
    profile = await db.get(StudyLearnerProfile, row.learner_profile_id)
    if (
        profile is None
        or profile.org_id != org.id
        or profile.status != "active"
        or profile.control_mode not in ("independent", "dependent")
    ):
        return None
    if profile.user_id != actor.id and not staff:
        recipient = (
            await db.execute(
                select(StudyReportRecipient).where(
                    StudyReportRecipient.org_id == org.id,
                    StudyReportRecipient.report_id == row.id,
                    StudyReportRecipient.recipient_user_id == actor.id,
                    StudyReportRecipient.projection == _REPORT_PROJECTION,
                    StudyReportRecipient.available_at <= now,
                )
            )
        ).scalar_one_or_none()
        if recipient is None:
            return None
        if recipient.recipient_capacity == "guardian_authority":
            if (
                profile.control_mode != StudyLearnerControlMode.DEPENDENT.value
                or recipient.relationship_id is None
            ):
                return None
            relationship = await db.get(StudyRelationship, recipient.relationship_id)
            if relationship is None or not await has_active_guardian_scope(
                db,
                org,
                profile,
                int(actor.id),
                recipient.projection,
                now=now,
                relationship_binding=(
                    relationship.relationship_uuid,
                    int(recipient.grant_version),
                ),
            ):
                return None
        else:
            if profile.control_mode != StudyLearnerControlMode.INDEPENDENT.value:
                return None
            grant = await current_independent_sharing_grant(
                db,
                org,
                profile,
                actor.id,
                recipient.projection,
                now=now,
            )
            if grant is None or (grant.id, grant.authority_version) != (
                recipient.sharing_grant_id,
                recipient.grant_version,
            ):
                return None
    if not await _is_learning_eligible(
        db, org, profile, now
    ) or not await has_current_consent_scope(
        db,
        org,
        profile,
        StudyConsentPurpose.LEARNING_TERMS,
        "adaptive_progress_report",
        now,
    ):
        return None
    try:
        curriculum, course = await _published_or_managed_curriculum(
            db, org, profile, revision_uuid, now
        )
        policies = await _report_policies(db, org, curriculum, now)
    except HTTPException as error:
        if error.status_code not in (403, 404, 409):
            raise
        return None
    if (
        policies != row.policy_version_ids
        or course.course_uuid != row.owner_course_uuid
    ):
        return None
    if not await _report_snapshot_is_current(
        db,
        row,
        profile,
        curriculum.id,
        course.id,
        course.course_uuid,
        now,
    ):
        return None
    return profile, curriculum, course


def _metadata(row, curriculum, course) -> ReportMetadata:
    return ReportMetadata(
        uuid=row.uuid,
        version=row.version,
        period_from=_as_utc(row.period_from),
        period_to=_as_utc(row.period_to),
        curriculum_title=curriculum.curriculum_key,
        course_title=course.name,
    )


class ReportRecipientOption(BaseModel):
    grant_uuid: str
    first_name: str


class ReportRecipientPage(BaseModel):
    items: list[ReportRecipientOption]
    next_offset: int | None


async def list_report_recipients(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    report_uuid: str,
    expected_version: int,
    offset: int,
) -> ReportRecipientPage:
    _, profile, _, _, _, now = await _authorize_current_approved_report(
        request, db, org, actor, report_uuid, expected_version
    )
    if profile.control_mode == StudyLearnerControlMode.DEPENDENT.value:
        rel = StudyRelationship
        query = (
            select(rel, User.first_name)
            .join(User, User.id == rel.adult_user_id)
            .where(
                rel.org_id == org.id,
                rel.learner_profile_id == profile.id,
                rel.relationship_type == StudyRelationshipType.GUARDIAN.value,
                rel.status == StudyRelationshipStatus.ACCEPTED.value,
                rel.valid_from <= now,
                rel.valid_to > now,
            )
        )
        candidates = (
            await db.execute(query.order_by(rel.relationship_uuid).offset(offset).limit(21))
        ).all()
        items = []
        for candidate, name in candidates[:20]:
            if await has_active_guardian_scope(
                db,
                org,
                profile,
                int(candidate.adult_user_id),
                _REPORT_PROJECTION,
                now=now,
                relationship_binding=(
                    candidate.relationship_uuid,
                    candidate.authority_version,
                ),
            ):
                items.append(
                    ReportRecipientOption(
                        grant_uuid=candidate.relationship_uuid,
                        first_name=name,
                    )
                )
        return ReportRecipientPage(
            items=items,
            next_offset=offset + 20 if len(candidates) > 20 else None,
        )
    grant = StudySharingGrant
    query = (
        select(grant, User.first_name)
        .join(User, User.id == grant.recipient_user_id)
        .where(
            grant.org_id == org.id,
            grant.learner_profile_id == profile.id,
            grant.status == "active",
            grant.valid_from <= now,
            grant.valid_to > now,
        )
    )
    candidates = (
        await db.execute(query.order_by(grant.grant_uuid).offset(offset).limit(21))
    ).all()
    items = []
    for candidate, name in candidates[:20]:
        matching = await current_independent_sharing_grant(
            db,
            org,
            profile,
            candidate.recipient_user_id,
            _REPORT_PROJECTION,
            now=now,
        )
        if matching is not None and matching.id == candidate.id:
            items.append(
                ReportRecipientOption(grant_uuid=candidate.grant_uuid, first_name=name)
            )
    return ReportRecipientPage(
        items=items,
        next_offset=offset + 20 if len(candidates) > 20 else None,
    )


class ReportRecipientPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Annotated[int, Field(strict=True, ge=1)]
    recipient_grant_uuid: Annotated[str, Field(min_length=1, max_length=100)]


async def preview_report_recipient(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    report_uuid: str,
    body: ReportRecipientPreview,
) -> PublishedReportRead:
    publish_body = (
        ReportPublish(
            expected_version=body.expected_version,
            guardian_relationship_uuids=[body.recipient_grant_uuid],
        )
        if body.recipient_grant_uuid.startswith("rel_")
        else ReportPublish(
            expected_version=body.expected_version,
            recipient_grant_uuids=[body.recipient_grant_uuid],
        )
    )
    row, profile, curriculum, course, _, _, _, _ = await _authorize_report_release(
        request,
        db,
        org,
        actor,
        report_uuid,
        publish_body,
    )
    return await _report_projection(
        db,
        row,
        profile,
        curriculum,
        course,
        recipient=True,
        can_request_review=True,
    )


async def _report_projection(
    db,
    row,
    profile,
    curriculum,
    course,
    *,
    recipient: bool,
    can_request_review: bool,
) -> PublishedReportRead:
    learner = await db.get(User, profile.user_id)
    if learner is None:
        raise HTTPException(404, "Learning record not found.")
    return PublishedReportRead(
        **_metadata(row, curriculum, course).model_dump(),
        learner_name=learner.first_name,
        can_request_review=can_request_review,
        evidence_cutoff=_as_utc(row.period_to),
        summary=ReportSummary.model_validate(row.summary),
        commentary=None if recipient else row.commentary,
    )


async def read_report(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    report_uuid: str,
) -> PublishedReportRead | ReportDraftRead:
    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    row, learner_uuid, revision_uuid = await _latest_report(db, org, report_uuid)
    authorized = await _published_report_context(
        db, org, actor, row, revision_uuid, now
    )
    staff = False
    if authorized is None:
        try:
            await _report_authority(
                request, db, org, actor, learner_uuid, revision_uuid, now
            )
        except HTTPException as error:
            if error.status_code not in (403, 404, 409):
                raise
            raise HTTPException(404, "Learning record not found.") from error
        if row.status == "withdrawn":
            raise HTTPException(404, "Learning record not found.")
        if row.status != "published":
            return _report_read(row, revision_uuid)
        staff = True
        authorized = await _published_report_context(
            db, org, actor, row, revision_uuid, now, staff=True
        )
    if authorized is None:
        raise HTTPException(404, "Learning record not found.")
    profile, curriculum, course = authorized
    return await _report_projection(
        db,
        row,
        profile,
        curriculum,
        course,
        recipient=not staff and profile.user_id != actor.id,
        can_request_review=not staff,
    )


def _report_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        date, uuid = urlsafe_b64decode(cursor.encode("ascii")).decode().split("|")
        timestamp = datetime.fromisoformat(date)
        if (
            timestamp.tzinfo is None
            or not uuid.startswith("report_")
            or len(uuid) != 43
        ):
            raise ValueError
        return _as_utc(timestamp), uuid
    except (ValueError, UnicodeError, Base64Error) as error:
        raise HTTPException(422, "Invalid report cursor.") from error


async def list_my_reports(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    cursor: str | None,
    limit: int,
) -> MyReportsPage:
    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    report, profile, recipient = (
        StudyProgressReport,
        StudyLearnerProfile,
        StudyReportRecipient,
    )
    successor = aliased(StudyProgressReport)
    query = (
        select(report, StudyCurriculumRevision.uuid)
        .join(profile, profile.id == report.learner_profile_id)
        .join(
            StudyCurriculumRevision,
            StudyCurriculumRevision.id == report.curriculum_revision_id,
        )
        .where(
            report.org_id == org.id,
            profile.org_id == org.id,
            StudyCurriculumRevision.org_id == org.id,
            report.status == "published",
            ~exists().where(
                successor.org_id == report.org_id,
                successor.uuid == report.uuid,
                successor.version > report.version,
            ),
            or_(
                profile.user_id == actor.id,
                exists().where(
                    recipient.org_id == org.id,
                    recipient.report_id == report.id,
                    recipient.recipient_user_id == actor.id,
                ),
            ),
        )
        .order_by(report.created_at.desc(), report.uuid.desc())
    )
    position = _report_cursor(cursor) if cursor else None
    found = []
    while True:
        batch = query
        if position is not None:
            stamp, uuid = position
            batch = batch.where(
                or_(
                    report.created_at < stamp,
                    and_(report.created_at == stamp, report.uuid < uuid),
                )
            )
        rows = (await db.execute(batch.limit(30))).all()
        for row, revision_uuid in rows:
            position = (_as_utc(row.created_at), row.uuid)
            scope = await _published_report_context(
                db, org, actor, row, revision_uuid, now
            )
            if scope is not None:
                _, curriculum, course = scope
                if len(found) == limit:
                    last = found[-1][1]
                    next_cursor = urlsafe_b64encode(
                        f"{last[0].isoformat()}|{last[1]}".encode()
                    ).decode()
                    return MyReportsPage(
                        items=[item[0] for item in found], next_cursor=next_cursor
                    )
                found.append((_metadata(row, curriculum, course), position))
        if len(rows) < 30:
            return MyReportsPage(items=[item[0] for item in found], next_cursor=None)


class ReportChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Annotated[int, Field(strict=True, ge=1)]
    reason: Literal[
        "evidence_corrected", "disclosure_authority", "report_error", "other"
    ]


class ReportWithdrawalReceipt(BaseModel):
    uuid: str
    version: int
    status: Literal["withdrawn"] = "withdrawn"
    notices_suppressed: int


async def change_published_report(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    report_uuid: str,
    body: ReportChange,
    *,
    action: Literal["withdraw", "supersede"],
) -> ReportDraftRead | ReportWithdrawalReceipt:
    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    row, learner_uuid, revision_uuid = await _latest_report(db, org, report_uuid)
    profile, curriculum, course, policy_ids = await _report_authority(
        request,
        db,
        org,
        actor,
        learner_uuid,
        revision_uuid,
        now,
    )
    statuses = ("published", "withdrawn") if action == "supersede" else ("published",)
    if row.version != body.expected_version or row.status not in statuses:
        raise studytomy_http_error(409, "STALE_VERSION", "Reload the current report.")
    start, end = _as_utc(row.period_from), _as_utc(row.period_to)
    digest, summary, sources = row.digest, row.summary, row.sources
    watermark = row.evidence_watermark
    if action == "supersede":
        watermark = await _watermark(db, org.id, profile.id)
        projection, sources = await _snapshot(
            db,
            org.id,
            profile,
            curriculum.id,
            course.id,
            course.course_uuid,
            start,
            end,
            watermark,
            now,
        )
        summary = projection.model_dump(mode="json")
        digest = canonical_request_hash(
            {
                "schema": "adaptive_progress_report_correction_v1",
                "previous_digest": row.digest,
                "reason": body.reason,
                "watermark": watermark,
                "policies": policy_ids,
                "summary": summary,
                "sources": sources,
            }
        )
    version = StudyProgressReport(
        uuid=row.uuid,
        org_id=org.id,
        learner_profile_id=profile.id,
        curriculum_revision_id=curriculum.id,
        owner_course_uuid=row.owner_course_uuid,
        requester_user_id=row.requester_user_id,
        period_from=start,
        period_to=end,
        evidence_watermark=watermark,
        policy_version_ids=row.policy_version_ids
        if action == "withdraw"
        else policy_ids,
        summary=summary,
        sources=sources,
        digest=digest,
        version=row.version + 1,
        status="withdrawn" if action == "withdraw" else "draft",
        commentary=row.commentary if action == "withdraw" else None,
        reviewer_user_id=row.reviewer_user_id if action == "withdraw" else None,
        approved_at=row.approved_at if action == "withdraw" else None,
        change_reason=body.reason,
        changed_by_user_id=actor.id,
    )
    db.add(version)
    suppressed = await db.execute(
        update(StudyOutboxRecord)
        .where(
            StudyOutboxRecord.org_id == org.id,
            StudyOutboxRecord.purpose == "progress_report",
            StudyOutboxRecord.resource_kind == "progress_report",
            StudyOutboxRecord.resource_uuid == row.uuid,
            StudyOutboxRecord.status.in_(("queued", "retry")),
        )
        .values(
            status="suppressed",
            completed_at=now,
            updated_at=now,
            last_error_code="REPORT_REPLACED",
        )
        .returning(StudyOutboxRecord.id)
    )
    count = len(suppressed.scalars().all())
    await db.flush()
    response = (
        ReportWithdrawalReceipt(
            uuid=version.uuid, version=version.version, notices_suppressed=count
        )
        if action == "withdraw"
        else _report_read(version, revision_uuid)
    )
    await db.commit()
    return response


class ReportReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Annotated[int, Field(strict=True, ge=1)]
    reason: Literal["evidence_error", "report_inaccurate", "privacy_concern", "other"]


class ReportReviewRequestReceipt(BaseModel):
    uuid: str
    report_version: int
    status: Literal["received"] = "received"


async def request_report_review(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    report_uuid: str,
    body: ReportReviewRequest,
) -> ReportReviewRequestReceipt:
    await lock_organization(db, org.id)
    row, _, revision_uuid = await _latest_report(db, org, report_uuid)
    if (
        await _published_report_context(
            db, org, actor, row, revision_uuid, datetime.now(UTC)
        )
        is None
    ):
        raise HTTPException(404, "Learning record not found.")
    if row.version != body.expected_version:
        raise studytomy_http_error(409, "STALE_VERSION", "Reload the current report.")
    record = (
        await db.execute(
            select(StudyReportReviewRequest).where(
                StudyReportReviewRequest.org_id == org.id,
                StudyReportReviewRequest.report_id == row.id,
                StudyReportReviewRequest.requester_user_id == actor.id,
                StudyReportReviewRequest.reason == body.reason,
            )
        )
    ).scalar_one_or_none()
    if record is None:
        record = StudyReportReviewRequest(
            org_id=org.id,
            report_id=row.id,
            requester_user_id=actor.id,
            reason=body.reason,
        )
        db.add(record)
    receipt = ReportReviewRequestReceipt(uuid=record.uuid, report_version=row.version)
    await db.commit()
    return receipt


class StaffReportReviewSignal(BaseModel):
    report_version: int
    reason: Literal["evidence_error", "report_inaccurate", "privacy_concern", "other"]
    count: int


class StaffReportReview(BaseModel):
    course_uuid: str
    report: ReportDraftRead
    review_state: Literal["current", "stale", "correction_pending", "withdrawn"]
    review_requests: list[StaffReportReviewSignal]


class StaffReportListItem(BaseModel):
    uuid: str
    version: int
    status: Literal["draft", "approved", "published", "withdrawn"]
    period_from: AwareDatetime
    period_to: AwareDatetime


class StaffReportList(BaseModel):
    items: list[StaffReportListItem]
    next_cursor: str | None


async def list_staff_reports(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    learner_uuid: str,
    revision_uuid: str,
    cursor: str | None,
    limit: int,
) -> StaffReportList:
    await lock_organization(db, org.id)
    profile, curriculum, course, _ = await _report_authority(
        request, db, org, actor, learner_uuid, revision_uuid, datetime.now(UTC)
    )
    report = StudyProgressReport
    successor = aliased(report)
    query = select(report).where(
        report.org_id == org.id,
        report.learner_profile_id == profile.id,
        report.curriculum_revision_id == curriculum.id,
        report.owner_course_uuid == course.course_uuid,
        ~exists().where(
            successor.org_id == report.org_id,
            successor.uuid == report.uuid,
            successor.version > report.version,
        ),
    )
    if cursor:
        timestamp, uuid = _report_cursor(cursor)
        query = query.where(
            or_(
                report.created_at < timestamp,
                (report.created_at == timestamp) & (report.uuid < uuid),
            )
        )
    rows = (
        (
            await db.execute(
                query.order_by(report.created_at.desc(), report.uuid.desc()).limit(
                    limit + 1
                )
            )
        )
        .scalars()
        .all()
    )
    visible = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        tail = visible[-1]
        next_cursor = urlsafe_b64encode(
            f"{_as_utc(tail.created_at).isoformat()}|{tail.uuid}".encode("ascii")
        ).decode("ascii")
    return StaffReportList(
        items=[
            StaffReportListItem(
                uuid=row.uuid,
                version=row.version,
                status=row.status,
                period_from=_as_utc(row.period_from),
                period_to=_as_utc(row.period_to),
            )
            for row in visible
        ],
        next_cursor=next_cursor,
    )


async def read_staff_report_review(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    report_uuid: str,
) -> StaffReportReview:
    await lock_organization(db, org.id)
    row, learner_uuid, revision_uuid = await _latest_report(db, org, report_uuid)
    now = datetime.now(UTC)
    profile, curriculum, course, policy_ids = await _report_authority(
        request, db, org, actor, learner_uuid, revision_uuid, now
    )
    if row.status == "withdrawn":
        review_state = "withdrawn"
    elif (
        policy_ids == row.policy_version_ids
        and course.course_uuid == row.owner_course_uuid
        and await _report_snapshot_is_current(
            db,
            row,
            profile,
            curriculum.id,
            course.id,
            course.course_uuid,
            now,
        )
    ):
        review_state = "current"
    else:
        review_state = "correction_pending" if row.status == "published" else "stale"
    report = StudyProgressReport
    review = StudyReportReviewRequest
    signals = (
        await db.execute(
            select(report.version, review.reason, func.count())
            .join(
                review,
                (review.report_id == report.id) & (review.org_id == report.org_id),
            )
            .where(report.org_id == org.id, report.uuid == row.uuid)
            .group_by(report.version, review.reason)
            .order_by(report.version.desc(), review.reason)
            .limit(20)
        )
    ).all()
    return StaffReportReview(
        course_uuid=row.owner_course_uuid,
        report=_report_read(row, revision_uuid),
        review_state=review_state,
        review_requests=[
            StaffReportReviewSignal(report_version=version, reason=reason, count=count)
            for version, reason, count in signals
        ],
    )
