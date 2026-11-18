"""Reviewed conditions for certificates tied to a managed course release."""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.education import StudyCourseRelease, StudyCourseReview
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import StudyBusinessEvent, StudyPolicyBody, StudyPolicyKey, StudyPolicyVersion, inspect_study_policy_body
from src.db.users import PublicUser
from src.security.studytomy_access import StudytomyCapabilityName, authorize_studytomy_staff_action
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.grading_contract import retained_grading_contract_digest
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization
from src.services.studytomy.prerequisites import ResourceUUID, course_completion_text_activities, retained_release_pass
from src.services.studytomy.record_requests import _self_profile

_DisplayName = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=160)]


class ManagedCertificateConditions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    award_name: _DisplayName
    issuer_name: _DisplayName
    verification: Literal["private"]
    issuance: Literal["reviewed"] | None = None
    changes: Literal["reviewed_assessment_corrections"] | None = None
    required_assessment_uuids: Annotated[list[ResourceUUID], Field(max_length=50)] = Field(default_factory=list)


_COURSE_CERTIFICATES = TypeAdapter(dict[ResourceUUID, ManagedCertificateConditions])


def _certificate_conditions(body: StudyPolicyBody, course_uuid: str) -> ManagedCertificateConditions | None:
    presentation = body.specified_value("certificate_presentation")
    if not isinstance(presentation, dict) or "managed_certificates_by_course" not in presentation:
        return None
    return _COURSE_CERTIFICATES.validate_python(presentation["managed_certificates_by_course"]).get(course_uuid)


def _retained_certificate_conditions(
    release: StudyCourseRelease, rights: StudyPolicyVersion | None,
) -> ManagedCertificateConditions | None:
    if rights is None or rights.org_id != release.org_id or rights.policy_key != StudyPolicyKey.D_16.value:
        return None
    versions = (release.policy_binding or {}).get("versions")
    pinned = versions.get(StudyPolicyKey.D_16.value) if isinstance(versions, dict) else None
    if (
        not isinstance(pinned, dict)
        or pinned.get("policy_id") != rights.id
        or pinned.get("body_sha256") != canonical_request_hash(rights.body)
    ):
        return None
    body, blockers = inspect_study_policy_body(StudyPolicyKey.D_16, rights.body)
    if body is None or any(blockers.values()):
        return None
    try:
        return _certificate_conditions(body, release.course_uuid)
    except ValueError:
        return None


def validate_release_certificate_conditions(
    presentation_policy: StudyPolicyBody, progression_policy: StudyPolicyBody, snapshot: dict,
) -> None:
    """Validate opt-in award conditions before retaining a reviewed release."""
    invalid = studytomy_http_error(
        409, "RELEASE_CERTIFICATE_CONDITIONS_INVALID",
        "A managed certificate needs reviewed completion requirements and valid assessments from this course.",
    )
    try:
        course_uuid = snapshot["course"]["course_uuid"]
        conditions = _certificate_conditions(presentation_policy, course_uuid)
        if conditions is None:
            return
        if not course_completion_text_activities(progression_policy, course_uuid):
            raise invalid
        required = conditions.required_assessment_uuids
        if len(set(required)) != len(required):
            raise invalid
        if not required:
            return

        assignments = {row["assignment_uuid"]: row for row in snapshot["assignments"]}
        assessment_activities = {
            row["id"] for row in snapshot["activities"]
            if row.get("published") is True and row.get("activity_sub_type") == "SUBTYPE_ASSIGNMENT_ANY"
        }
        for uuid in required:
            assignment = assignments.get(uuid)
            if (
                assignment is None
                or assignment.get("published") is not True
                or assignment.get("ungraded") is True
                or assignment.get("activity_id") not in assessment_activities
            ):
                raise invalid
            retained_grading_contract_digest(assignment, snapshot["assignment_tasks"])
    except (KeyError, TypeError, ValueError) as error:
        raise invalid from error


class ManagedCertificateEligibility(BaseModel):
    course_uuid: str
    release_uuid: str
    eligible: bool
    completion_event_uuid: str | None
    assessment_result_uuids: dict[str, str]
    missing_assessment_uuids: list[str]
    issuance_enabled: bool
    evidence_sha256: str


async def preview_managed_certificate_eligibility(
    db: AsyncSession, org: Organization, reviewer: PublicUser,
    course_uuid: str, release_uuid: str, learner_uuid: str,
) -> ManagedCertificateEligibility:
    """Select evidence for an authorized reviewer without issuing an award."""
    preview, _, _ = await _authorized_certificate_evidence(
        db, org, reviewer, course_uuid, release_uuid, learner_uuid,
    )
    return preview


async def _authorized_certificate_evidence(
    db: AsyncSession, org: Organization, reviewer: PublicUser,
    course_uuid: str, release_uuid: str, learner_uuid: str,
    *,
    reviewing_existing_award: bool = False,
) -> tuple[ManagedCertificateEligibility, list[int], ManagedCertificateConditions]:
    from src.services.studytomy.releases import load_release_policy_binding

    org = await lock_organization(db, org.id)
    profile = await db.scalar(select(StudyLearnerProfile).where(
        StudyLearnerProfile.org_id == org.id,
        StudyLearnerProfile.learner_uuid == learner_uuid,
    ))
    unavailable = studytomy_http_error(404, "MANAGED_CERTIFICATE_UNAVAILABLE", "Certificate review unavailable.")
    if profile is None or profile.user_id == reviewer.id:
        raise unavailable
    now = datetime.now(UTC)
    for scope, resource in (("course", course_uuid), ("learner", learner_uuid)):
        await authorize_studytomy_staff_action(
            db, reviewer, org, StudytomyCapabilityName.ASSESSMENT_MODERATION,
            target_scope_kind=scope, target_resource_uuid=resource,
            subject_user_id=profile.user_id, hide_existence_on_denial=True, now=now,
        )
    release = await db.scalar(
        select(StudyCourseRelease)
        .join(StudyCourseReview, StudyCourseReview.id == StudyCourseRelease.review_id)
        .where(
            StudyCourseRelease.org_id == org.id, StudyCourseRelease.course_uuid == course_uuid,
            StudyCourseRelease.uuid == release_uuid, StudyCourseReview.org_id == org.id,
            StudyCourseReview.course_uuid == course_uuid, StudyCourseReview.decision == "approved",
            StudyCourseReview.revision_sha256 == StudyCourseRelease.snapshot_sha256,
        )
    )
    if release is None or canonical_request_hash(release.snapshot) != release.snapshot_sha256:
        raise unavailable
    rights = await db.get(StudyPolicyVersion, release.rights_policy_id)
    conditions = _retained_certificate_conditions(release, rights)
    if conditions is None:
        raise unavailable
    if reviewing_existing_award:
        retention, current_rights = await _current_policies(
            db, org, (StudyPolicyKey.D_13, StudyPolicyKey.D_16), now,
        )
        current_body, blockers = inspect_study_policy_body(StudyPolicyKey.D_16, current_rights.body)
        try:
            current_conditions = _certificate_conditions(current_body, course_uuid) if current_body else None
        except ValueError:
            raise unavailable from None
        if (
            any(blockers.values()) or current_conditions is None
            or current_conditions.changes != "reviewed_assessment_corrections"
        ):
            raise unavailable
        policy_refs = sorted({retention.id, current_rights.id, rights.id})
    else:
        binding = await load_release_policy_binding(db, org, now, candidate_snapshot=release.snapshot)
        if release.policy_binding != binding.model_dump(mode="json"):
            raise unavailable
        policy_refs = sorted(version.policy_id for version in binding.versions.values())

    completion = await db.scalar(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id,
        StudyBusinessEvent.actor_user_id == profile.user_id,
        StudyBusinessEvent.resource_kind == "course_release",
        StudyBusinessEvent.resource_uuid == release.uuid,
        StudyBusinessEvent.action == "studytomy.course.completion_recorded",
        StudyBusinessEvent.dedupe_key == f"studytomy:completion:{org.id}:{profile.id}:{release.uuid}",
    ))
    completion_uuid = (
        completion.event_uuid if completion is not None
        and completion.policy_refs == [release.progression_policy_id]
        and completion.after_state == {"source_snapshot_digest": release.snapshot_sha256} else None
    )
    results = {}
    missing = []
    for source in sorted(conditions.required_assessment_uuids):
        result = await retained_release_pass(db, release, profile.user_id, source, lock_result=True)
        if result is None:
            missing.append(source)
        else:
            results[source] = result.event_uuid
    evidence_sha256 = canonical_request_hash({
        "org_id": org.id, "learner_uuid": learner_uuid, "release_uuid": release.uuid,
        "release_sha256": release.snapshot_sha256, "policy_binding": release.policy_binding,
        "conditions": conditions.model_dump(mode="json"),
        "completion_event_uuid": completion_uuid, "assessment_result_uuids": results,
    })
    preview = ManagedCertificateEligibility(
        course_uuid=course_uuid, release_uuid=release_uuid, eligible=completion_uuid is not None and not missing,
        completion_event_uuid=completion_uuid, assessment_result_uuids=results, missing_assessment_uuids=missing,
        issuance_enabled=conditions.issuance == "reviewed", evidence_sha256=evidence_sha256,
    )
    return preview, policy_refs, conditions


class ManagedCertificateIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ManagedCertificateIssueReceipt(BaseModel):
    award_uuid: str
    release_uuid: str
    issued_at: datetime


async def issue_managed_certificate(
    db: AsyncSession, org: Organization, reviewer: PublicUser,
    course_uuid: str, release_uuid: str, learner_uuid: str, command: ManagedCertificateIssue,
) -> ManagedCertificateIssueReceipt:
    """Issue one immutable award from the exact evidence affirmed by the reviewer."""
    evidence, policy_refs, _ = await _authorized_certificate_evidence(
        db, org, reviewer, course_uuid, release_uuid, learner_uuid,
    )
    if not evidence.issuance_enabled:
        raise studytomy_http_error(409, "MANAGED_CERTIFICATE_ISSUANCE_UNAPPROVED", "Certificate issuance requires release policy approval.")
    key = f"studytomy:certificate:{org.id}:{release_uuid}:{learner_uuid}"
    award = await db.scalar(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.action == "studytomy.award.issued",
        StudyBusinessEvent.dedupe_key == key,
    ))
    if award is not None:
        if (award.after_state or {}).get("evidence_sha256") != command.evidence_sha256:
            raise studytomy_http_error(409, "MANAGED_CERTIFICATE_ALREADY_ISSUED", "A certificate was issued from different evidence.")
    else:
        if not evidence.eligible or command.evidence_sha256 != evidence.evidence_sha256:
            raise studytomy_http_error(409, "MANAGED_CERTIFICATE_EVIDENCE_CHANGED", "Review the current eligible evidence before issuing.")
        award = await append_business_event(
            db, org_id=org.id, actor_user_id=reviewer.id, actor_capacity="assessment_moderator",
            action="studytomy.award.issued", reason_code="REVIEWED_CERTIFICATE_ISSUED",
            subject_uuid=learner_uuid, resource_kind="course_release", resource_uuid=release_uuid,
            dedupe_key=key, policy_refs=policy_refs,
            after_state={
                "evidence_sha256": evidence.evidence_sha256,
                "completion_event_uuid": evidence.completion_event_uuid,
                "assessment_result_event_uuids": sorted(evidence.assessment_result_uuids.values()),
            },
        )
    return ManagedCertificateIssueReceipt(
        award_uuid=award.event_uuid, release_uuid=release_uuid, issued_at=award.created_at,
    )


_AWARD_CHANGED = "studytomy.award.assessment_reviewed"


async def _latest_award_change(db: AsyncSession, award: StudyBusinessEvent) -> StudyBusinessEvent | None:
    return await db.scalar(
        select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == award.org_id,
            StudyBusinessEvent.resource_kind == "managed_award",
            StudyBusinessEvent.resource_uuid == award.event_uuid,
            StudyBusinessEvent.subject_uuid == award.subject_uuid,
            StudyBusinessEvent.action == _AWARD_CHANGED,
        ).order_by(StudyBusinessEvent.id.desc()).limit(1)
    )


class ManagedCertificateChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corrected_result_uuid: UUID


class ManagedCertificateChangeReceipt(BaseModel):
    award_uuid: str
    review_uuid: str
    status: Literal["corrected", "withdrawn"]
    version: int


async def review_managed_certificate_change(
    db: AsyncSession, org: Organization, reviewer: PublicUser,
    course_uuid: str, release_uuid: str, learner_uuid: str, award_uuid: UUID,
    command: ManagedCertificateChange,
) -> ManagedCertificateChangeReceipt:
    """Apply a reviewed academic correction to an existing award reference."""
    from src.services.studytomy.grading import APPEAL_CORRECTION_REASON, RESULT_RECORDED_ACTION

    evidence, policy_refs, conditions = await _authorized_certificate_evidence(
        db, org, reviewer, course_uuid, release_uuid, learner_uuid, reviewing_existing_award=True,
    )
    unavailable = studytomy_http_error(404, "MANAGED_CERTIFICATE_UNAVAILABLE", "Certificate review unavailable.")
    if conditions.changes != "reviewed_assessment_corrections":
        raise unavailable
    award = await db.scalar(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id,
        StudyBusinessEvent.event_uuid == str(award_uuid),
        StudyBusinessEvent.subject_uuid == learner_uuid,
        StudyBusinessEvent.resource_kind == "course_release",
        StudyBusinessEvent.resource_uuid == release_uuid,
        StudyBusinessEvent.action == "studytomy.award.issued",
    ))
    if award is None:
        raise unavailable
    key = f"studytomy:award:review:{org.id}:{award_uuid}:{command.corrected_result_uuid}"
    change = await db.scalar(select(StudyBusinessEvent).where(
        StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.dedupe_key == key,
        StudyBusinessEvent.action == _AWARD_CHANGED,
    ))
    if change is None:
        previous = await _latest_award_change(db, award)
        state = (previous or award).after_state or {}
        conflict = studytomy_http_error(
            409, "MANAGED_CERTIFICATE_CORRECTION_UNVERIFIED",
            "An applicable finalized assessment correction is required.",
        )
        if state.get("status") == "withdrawn":
            raise conflict
        selected = state.get("assessment_result_event_uuids", [])
        result = await db.scalar(select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.event_uuid == str(command.corrected_result_uuid),
            StudyBusinessEvent.resource_kind == "assignment_round",
            StudyBusinessEvent.action == RESULT_RECORDED_ACTION,
            StudyBusinessEvent.reason == APPEAL_CORRECTION_REASON,
        ))
        corrected = result.after_state if result is not None else None
        if (
            not isinstance(corrected, dict)
            or corrected.get("previous_event_uuid") not in selected
            or type(corrected.get("passed")) is not bool
        ):
            raise conflict
        prior = await db.scalar(select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.event_uuid == corrected["previous_event_uuid"],
            StudyBusinessEvent.resource_uuid == result.resource_uuid,
            StudyBusinessEvent.action == RESULT_RECORDED_ACTION,
        ))
        prior_contract = (prior.after_state or {}).get("grading_contract_event_uuid") if prior else None
        if not prior_contract or corrected.get("grading_contract_event_uuid") != prior_contract:
            raise conflict
        appeal_outcome = await db.scalar(select(StudyBusinessEvent.id).where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.action == "studytomy.assessment.appeal_corrected",
            StudyBusinessEvent.subject_uuid == learner_uuid,
            StudyBusinessEvent.operation_uuid == result.resource_uuid,
            StudyBusinessEvent.after_state["result_uuid"].as_string() == result.event_uuid,
        ).limit(1))
        if appeal_outcome is None:
            raise conflict
        # Follow the exact reviewed result lineage, regardless of later retries.
        newest_result_id = await db.scalar(select(StudyBusinessEvent.id).where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.resource_uuid == result.resource_uuid,
            StudyBusinessEvent.action == RESULT_RECORDED_ACTION,
        ).order_by(StudyBusinessEvent.id.desc()).limit(1))
        if newest_result_id != result.id:
            raise conflict
        updated = sorted((set(selected) - {corrected["previous_event_uuid"]}) | {result.event_uuid})
        if corrected["passed"] and (
            not evidence.eligible or sorted(evidence.assessment_result_uuids.values()) != updated
        ):
            raise conflict
        status = "corrected" if corrected["passed"] else "withdrawn"
        change = await append_business_event(
            db, org_id=org.id, actor_user_id=reviewer.id, actor_capacity="assessment_moderator",
            subject_uuid=learner_uuid, resource_kind="managed_award", resource_uuid=award.event_uuid,
            action=_AWARD_CHANGED, reason_code="AWARD_ASSESSMENT_CORRECTION" if corrected["passed"]
            else "AWARD_WITHDRAWN_AFTER_ASSESSMENT_CORRECTION",
            dedupe_key=key, policy_refs=policy_refs,
            after_state={
                "status": status, "version": state.get("version", 1) + 1,
                "previous_event_uuid": (previous or award).event_uuid,
                "assessment_result_event_uuids": updated,
            },
        )
    return ManagedCertificateChangeReceipt(
        award_uuid=award.event_uuid, review_uuid=change.event_uuid,
        status=change.after_state["status"], version=change.after_state["version"],
    )


class ManagedCertificateVerification(ManagedCertificateIssueReceipt):
    award_name: str
    issuer_name: str
    course_name: str
    status: Literal["issued", "corrected", "withdrawn"]
    version: int


async def verify_my_managed_certificate(
    db: AsyncSession, org: Organization, actor: PublicUser, award_uuid: UUID,
) -> ManagedCertificateVerification:
    """Read the learner's own immutable award, independent of course access."""
    profile = await _self_profile(db, org, actor)
    unavailable = studytomy_http_error(404, "MANAGED_CERTIFICATE_UNAVAILABLE", "Certificate unavailable.")
    row = (await db.execute(_owned_award_query(org.id, profile.learner_uuid).where(
        StudyBusinessEvent.event_uuid == str(award_uuid),
    ))).one_or_none()
    verified = await _project_certificate_verification(db, *row) if row is not None else None
    if verified is None:
        raise unavailable
    return verified


def _owned_award_query(org_id: int, learner_uuid: str):
    return (
        select(StudyBusinessEvent, StudyCourseRelease, StudyPolicyVersion)
        .select_from(StudyBusinessEvent)
        .join(StudyCourseRelease, StudyCourseRelease.uuid == StudyBusinessEvent.resource_uuid)
        .join(StudyCourseReview, StudyCourseReview.id == StudyCourseRelease.review_id)
        .join(StudyPolicyVersion, StudyPolicyVersion.id == StudyCourseRelease.rights_policy_id)
        .where(
            StudyBusinessEvent.org_id == org_id,
            StudyBusinessEvent.subject_uuid == learner_uuid,
            StudyBusinessEvent.resource_kind == "course_release",
            StudyBusinessEvent.action == "studytomy.award.issued",
            StudyCourseRelease.org_id == org_id,
            StudyCourseReview.org_id == org_id,
            StudyCourseReview.course_uuid == StudyCourseRelease.course_uuid,
            StudyCourseReview.decision == "approved",
            StudyCourseReview.revision_sha256 == StudyCourseRelease.snapshot_sha256,
            StudyPolicyVersion.org_id == org_id,
            StudyPolicyVersion.policy_key == StudyPolicyKey.D_16.value,
        )
    )


async def _project_certificate_verification(
    db: AsyncSession, award: StudyBusinessEvent, release: StudyCourseRelease, rights: StudyPolicyVersion,
) -> ManagedCertificateVerification | None:
    if canonical_request_hash(release.snapshot) != release.snapshot_sha256:
        return None
    if rights.id not in (award.policy_refs or []):
        return None
    conditions = _retained_certificate_conditions(release, rights)
    course = release.snapshot.get("course")
    course_name = course.get("name") if isinstance(course, dict) else None
    if conditions is None or conditions.issuance != "reviewed" or not isinstance(course_name, str) or not course_name.strip():
        return None
    change = await _latest_award_change(db, award)
    return ManagedCertificateVerification(
        award_uuid=award.event_uuid, release_uuid=release.uuid, issued_at=award.created_at,
        award_name=conditions.award_name, issuer_name=conditions.issuer_name, course_name=course_name,
        status=change.after_state["status"] if change else "issued",
        version=change.after_state["version"] if change else 1,
    )


class ManagedCertificatePage(BaseModel):
    awards: list[ManagedCertificateVerification]
    next_cursor: str | None


async def list_my_managed_certificates(
    db: AsyncSession, org: Organization, actor: PublicUser, after: UUID | None = None,
) -> ManagedCertificatePage:
    profile = await _self_profile(db, org, actor)
    query = _owned_award_query(org.id, profile.learner_uuid)
    if after is not None:
        cursor = await db.scalar(select(StudyBusinessEvent.id).where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.subject_uuid == profile.learner_uuid,
            StudyBusinessEvent.action == "studytomy.award.issued",
            StudyBusinessEvent.event_uuid == str(after),
        ))
        if cursor is None:
            raise studytomy_http_error(404, "MANAGED_CERTIFICATE_UNAVAILABLE", "Certificate page unavailable.")
        query = query.where(StudyBusinessEvent.id < cursor)
    page_size = 20
    rows = (await db.execute(query.order_by(StudyBusinessEvent.id.desc()).limit(page_size + 1))).all()
    awards = []
    for row in rows[:page_size]:
        verified = await _project_certificate_verification(db, *row)
        if verified is not None:
            awards.append(verified)
    return ManagedCertificatePage(
        awards=awards,
        next_cursor=rows[page_size - 1][0].event_uuid if len(rows) > page_size else None,
    )
