"""Transactional adaptive evidence and scoped mastery reconciliation."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.adaptive import StudyConcept, StudyCurriculumRevision
from src.db.studytomy.adaptive_diagnostics import StudyDiagnosticRevision
from src.db.studytomy.adaptive_evidence import (
    StudyAttemptItem,
    StudyConceptMastery,
    StudyLearningAttempt,
    StudyLearningEvidence,
)
from src.db.studytomy.adaptive_items import StudyItemRevision
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import StudyPolicyBody, StudyPolicyKey
from src.db.users import PublicUser
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_studytomy_staff_action,
)
from src.services.studytomy.adaptive_mastery import (
    MasteryObservation,
    MasteryPolicy,
    MasteryResult,
    _utc,
    assess_mastery_window,
    resolve_mastery_policy,
)
from src.services.studytomy.adaptive_objective_scoring import (
    OBJECTIVE_EVALUATOR,
    InvalidObjectiveResponse,
    ObjectiveResult,
    evaluate_objective_item,
)
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.errors import (
    POLICY_NOT_APPROVED,
    STALE_VERSION,
    studytomy_http_error,
)
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization


@dataclass(frozen=True, slots=True)
class EvidenceResult:
    outcome: Literal["correct", "incorrect", "not_scored"]
    independence: Literal["independent", "assisted", "unknown"]
    eligible: bool
    eligibility_reason: str
    misconception_code: str | None = None

    @property
    def score(self) -> int | None:
        return {"correct": 1, "incorrect": 0, "not_scored": None}[self.outcome]


@dataclass(frozen=True, slots=True)
class MasteryIntegrity:
    status: Literal["current", "missing", "mismatch", "repaired"]
    evidence_watermark: int
    projection: MasteryResult
    projection_version: int | None


async def _watermark(db: AsyncSession, org_id: int, learner_id: int) -> int:
    return (
        await db.execute(
            select(
                func.coalesce(func.max(StudyLearningEvidence.evidence_sequence), 0)
            ).where(
                StudyLearningEvidence.org_id == org_id,
                StudyLearningEvidence.learner_profile_id == learner_id,
            )
        )
    ).scalar_one()


async def _bounded_mastery(
    db: AsyncSession,
    org_id: int,
    learner_id: int,
    concept_id: int,
    policy: MasteryPolicy,
    watermark: int,
    now: datetime,
) -> MasteryResult:
    """Select one result per logical observation and a bounded independent-family window in SQL."""
    journal = (
        select(
            StudyLearningEvidence.logical_source_uuid,
            StudyLearningEvidence.result_revision,
            StudyLearningEvidence.evidence_sequence,
            StudyLearningEvidence.outcome,
            StudyLearningEvidence.independence,
            StudyLearningEvidence.eligible,
            StudyLearningEvidence.is_retraction,
            StudyLearningEvidence.observed_at,
            StudyLearningEvidence.evaluator_version.label("result_evaluator"),
            StudyItemRevision.evaluator_version.label("item_evaluator"),
            StudyAttemptItem.item_family_uuid,
            StudyLearningAttempt.uuid.label("session_uuid"),
            StudyLearningAttempt.started_at.label("session_started_at"),
            func.row_number()
            .over(
                partition_by=StudyLearningEvidence.logical_source_uuid,
                order_by=StudyLearningEvidence.result_revision.desc(),
            )
            .label("revision_rank"),
        )
        .select_from(StudyLearningEvidence)
        .join(
            StudyAttemptItem,
            StudyAttemptItem.id == StudyLearningEvidence.attempt_item_id,
        )
        .join(
            StudyLearningAttempt,
            StudyLearningAttempt.id == StudyAttemptItem.attempt_id,
        )
        .join(
            StudyItemRevision, StudyItemRevision.id == StudyAttemptItem.item_revision_id
        )
        .where(
            StudyLearningEvidence.org_id == org_id,
            StudyLearningEvidence.learner_profile_id == learner_id,
            StudyLearningEvidence.concept_id == concept_id,
            StudyLearningEvidence.evidence_sequence <= watermark,
        )
        .cte("mastery_journal")
    )
    current = (
        select(journal)
        .where(journal.c.revision_rank == 1, journal.c.is_retraction.is_(False))
        .cte("mastery_current")
    )
    assisted, unknown = (
        await db.execute(
            select(
                func.count().filter(current.c.independence == "assisted"),
                func.count().filter(current.c.independence == "unknown"),
            ).select_from(current)
        )
    ).one()
    ranked = (
        select(
            current,
            func.row_number()
            .over(
                partition_by=current.c.item_family_uuid,
                order_by=(
                    current.c.observed_at.desc(),
                    current.c.evidence_sequence.desc(),
                ),
            )
            .label("family_rank"),
        )
        .where(
            current.c.eligible.is_(True),
            current.c.independence == "independent",
            current.c.outcome.in_(("correct", "incorrect")),
            current.c.result_evaluator == current.c.item_evaluator,
            current.c.item_evaluator == OBJECTIVE_EVALUATOR,
        )
        .cte("mastery_families")
    )
    rows = (
        await db.execute(
            select(ranked)
            .where(ranked.c.family_rank == 1)
            .order_by(
                ranked.c.observed_at.desc(),
                ranked.c.evidence_sequence.desc(),
            )
            .limit(policy.window_size)
        )
    ).all()
    evidence = tuple(
        MasteryObservation(
            logical_source_uuid=row.logical_source_uuid,
            result_revision=row.result_revision,
            evidence_sequence=row.evidence_sequence,
            item_family_uuid=row.item_family_uuid,
            session_uuid=row.session_uuid,
            session_started_at=row.session_started_at,
            observed_at=row.observed_at,
            outcome=row.outcome,
            independence=row.independence,
            eligible=row.eligible,
        )
        for row in rows
    )
    return assess_mastery_window(
        policy, evidence, assisted_count=assisted, unknown_count=unknown, now=now
    )


async def _mastery_snapshot(
    db: AsyncSession,
    profile: StudyLearnerProfile,
    concept: StudyConcept,
    policy: MasteryPolicy,
    now: datetime,
) -> tuple[StudyConceptMastery | None, int, MasteryResult]:
    watermark = await _watermark(db, profile.org_id, profile.id)
    row = (
        await db.execute(
            select(StudyConceptMastery)
            .where(
                StudyConceptMastery.org_id == profile.org_id,
                StudyConceptMastery.learner_profile_id == profile.id,
                StudyConceptMastery.concept_id == concept.id,
                StudyConceptMastery.mastery_policy_digest == policy.digest,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    result = await _bounded_mastery(
        db, profile.org_id, profile.id, concept.id, policy, watermark, now
    )
    return row, watermark, result


def _matches(row: StudyConceptMastery, watermark: int, result: MasteryResult) -> bool:
    for field, value in asdict(result).items():
        old = getattr(row, field)
        if field == "latest_independent_at" and old is not None:
            old = _utc(old)
        if old != value:
            return False
    return row.evidence_watermark == watermark


async def _store_projection(
    db: AsyncSession,
    profile: StudyLearnerProfile,
    concept: StudyConcept,
    policy: MasteryPolicy,
    row: StudyConceptMastery | None,
    watermark: int,
    result: MasteryResult,
    now: datetime,
) -> StudyConceptMastery:
    values = asdict(result) | {"evidence_watermark": watermark, "computed_at": now}
    if row is None:
        row = StudyConceptMastery(
            org_id=profile.org_id,
            learner_profile_id=profile.id,
            curriculum_revision_id=concept.curriculum_revision_id,
            concept_id=concept.id,
            mastery_policy_uuid=policy.policy_uuid,
            mastery_policy_digest=policy.digest,
            reducer_version=policy.reducer_version,
            **values,
        )
        db.add(row)
    elif not _matches(row, watermark, result):
        row.sqlmodel_update(values | {"version": row.version + 1})
    await db.flush()
    return row


def _objective_result(
    issued: StudyAttemptItem,
    attempt: StudyLearningAttempt,
    item: StudyItemRevision,
    declared_external_help: bool = False,
) -> EvidenceResult:
    if issued.response_state == "skipped":
        return EvidenceResult("not_scored", "unknown", False, "skipped")
    if issued.response_state != "answered":
        raise studytomy_http_error(
            409, STALE_VERSION, "Only an accepted response can produce evidence."
        )
    payload = {
        "kind": item.kind,
        (
            "option_uuid" if item.kind == "single_choice" else "value"
        ): issued.normalized_response,
    }
    try:
        result = evaluate_objective_item(item, payload)
    except InvalidObjectiveResponse:
        # The accepted snapshot is retained for investigation. Never turn it into a wrong answer.
        result = ObjectiveResult("not_scored")
    if result.outcome == "not_scored":
        return EvidenceResult("not_scored", "unknown", False, "unsupported_rubric")
    if attempt.kind == "practice":
        return EvidenceResult(
            result.outcome, "assisted", False, "supported_practice", result.error_code
        )
    if declared_external_help or issued.help_count or issued.exposure_count:
        return EvidenceResult(
            result.outcome, "assisted", False, "help_or_exposure", result.error_code
        )
    if attempt.kind != "diagnostic" or item.source_exposure != "private_original":
        return EvidenceResult(
            result.outcome,
            "unknown",
            False,
            "independence_unverified",
            result.error_code,
        )
    return EvidenceResult(
        result.outcome, "independent", True, "independent_diagnostic", result.error_code
    )


async def append_objective_evidence(
    db: AsyncSession,
    org_id: int,
    actor: PublicUser,
    issued_uuid: str,
    policy: MasteryPolicy,
    policy_version_ids: list[int],
    *,
    declared_external_help: bool = False,
) -> StudyLearningEvidence:
    """Append within the caller's transaction. The caller must authorize and lock the learner before accepting a response."""
    learner_id = (
        await db.execute(
            select(StudyAttemptItem.learner_profile_id).where(
                StudyAttemptItem.org_id == org_id,
                StudyAttemptItem.uuid == issued_uuid,
            )
        )
    ).scalar_one_or_none()
    profile = (
        await db.execute(
            select(StudyLearnerProfile)
            .where(
                StudyLearnerProfile.org_id == org_id,
                StudyLearnerProfile.id == learner_id,
                StudyLearnerProfile.user_id == actor.id,
                StudyLearnerProfile.status == "active",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if profile is None:
        raise HTTPException(404, "Issued response not found.")
    source = (
        await db.execute(
            select(
                StudyAttemptItem,
                StudyLearningAttempt,
                StudyItemRevision,
                StudyConcept,
            )
            .select_from(StudyAttemptItem)
            .join(
                StudyLearningAttempt,
                StudyLearningAttempt.id == StudyAttemptItem.attempt_id,
            )
            .join(
                StudyItemRevision,
                StudyItemRevision.id == StudyAttemptItem.item_revision_id,
            )
            .join(StudyConcept, StudyConcept.id == StudyAttemptItem.concept_id)
            .where(
                StudyAttemptItem.org_id == org_id,
                StudyAttemptItem.learner_profile_id == profile.id,
                StudyAttemptItem.uuid == issued_uuid,
                StudyLearningAttempt.actor_capacity == "learner",
                StudyLearningAttempt.actor_user_id == actor.id,
                StudyLearningAttempt.kind.in_(("diagnostic", "practice")),
            )
            .with_for_update(of=(StudyLearningAttempt, StudyAttemptItem))
        )
    ).one_or_none()
    if source is None:
        raise HTTPException(404, "Issued response not found.")
    issued, attempt, item, concept = source
    if attempt.kind == "diagnostic":
        definition = await db.get(
            StudyDiagnosticRevision, attempt.diagnostic_revision_id
        )
        if (
            definition is None
            or definition.mastery_policy_uuid != policy.policy_uuid
            or (definition.manifest.get("mastery_policy_digest") != policy.digest)
        ):
            raise studytomy_http_error(
                409, POLICY_NOT_APPROVED, "Use the diagnostic's frozen mastery policy."
            )
    result = _objective_result(issued, attempt, item, declared_external_help)
    evidence = StudyLearningEvidence(
        org_id=org_id,
        learner_profile_id=profile.id,
        curriculum_revision_id=concept.curriculum_revision_id,
        concept_id=concept.id,
        attempt_item_id=issued.id,
        logical_source_uuid=issued.uuid,
        evaluator_version=item.evaluator_version,
        policy_version_ids=policy_version_ids,
        actor_user_id=actor.id,
        observed_at=issued.responded_at,
        score=result.score,
        **asdict(result),
    )
    accepted, _projection = await append_evidence_and_project(
        db, profile, concept, evidence, policy
    )
    return accepted


def _evidence_payload(evidence: StudyLearningEvidence) -> dict:
    payload = evidence.model_dump(
        exclude={"id", "uuid", "created_at", "evidence_sequence"}
    )
    payload["observed_at"] = _utc(evidence.observed_at)
    return payload


async def append_evidence_and_project(
    db: AsyncSession,
    profile: StudyLearnerProfile,
    concept: StudyConcept,
    evidence: StudyLearningEvidence,
    policy: MasteryPolicy,
    *,
    now: datetime | None = None,
) -> tuple[StudyLearningEvidence, StudyConceptMastery]:
    """Internal commit unit for a server-authorized result. Caller holds the learner lock."""
    if (evidence.org_id, evidence.learner_profile_id) != (
        profile.org_id,
        profile.id,
    ) or (evidence.org_id, evidence.curriculum_revision_id, evidence.concept_id) != (
        concept.org_id,
        concept.curriculum_revision_id,
        concept.id,
    ):
        raise ValueError(
            "Evidence and projection must have the same learner and concept."
        )
    await db.execute(
        select(StudyLearnerProfile.id)
        .where(
            StudyLearnerProfile.org_id == profile.org_id,
            StudyLearnerProfile.id == profile.id,
        )
        .with_for_update()
    )
    existing = (
        await db.execute(
            select(StudyLearningEvidence).where(
                StudyLearningEvidence.org_id == profile.org_id,
                StudyLearningEvidence.learner_profile_id == profile.id,
                StudyLearningEvidence.logical_source_uuid
                == evidence.logical_source_uuid,
                StudyLearningEvidence.result_revision == evidence.result_revision,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if _evidence_payload(existing) != _evidence_payload(evidence):
            raise studytomy_http_error(
                409,
                STALE_VERSION,
                "The evidence revision already has a different result.",
            )
        evidence = existing
    else:
        if db.get_bind().dialect.name == "sqlite":
            evidence.evidence_sequence = (
                await _watermark(db, profile.org_id, profile.id) + 1
            )
        db.add(evidence)
        await db.flush()
    current_time = now or datetime.now(UTC)
    row, watermark, result = await _mastery_snapshot(
        db, profile, concept, policy, current_time
    )
    projection = await _store_projection(
        db, profile, concept, policy, row, watermark, result, current_time
    )
    return evidence, projection


async def reconcile_mastery(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    learner_uuid: str,
    concept_uuid: str,
    policy_uuid: str,
    *,
    repair: bool = False,
) -> MasteryIntegrity:
    """Explicit staff-scoped integrity check or repair. Never edits source evidence."""
    await lock_organization(db, org.id)
    profile = (
        await db.execute(
            select(StudyLearnerProfile)
            .where(
                StudyLearnerProfile.org_id == org.id,
                StudyLearnerProfile.learner_uuid == learner_uuid,
                StudyLearnerProfile.status == "active",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    selected = (
        await db.execute(
            select(StudyConcept, StudyCurriculumRevision)
            .join(
                StudyCurriculumRevision,
                StudyCurriculumRevision.id == StudyConcept.curriculum_revision_id,
            )
            .where(StudyConcept.org_id == org.id, StudyConcept.uuid == concept_uuid)
        )
    ).one_or_none()
    if profile is None or selected is None or not selected[1].owner_course_uuid:
        raise HTTPException(404, "Mastery projection not found.")
    concept, curriculum = selected
    courses = (
        (
            await db.execute(
                select(Course.id)
                .where(
                    Course.org_id == org.id,
                    Course.course_uuid == curriculum.owner_course_uuid,
                )
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    if len(courses) != 1:
        raise HTTPException(404, "Mastery projection not found.")
    now = datetime.now(UTC)
    for kind, uuid in (
        ("course", curriculum.owner_course_uuid),
        ("learner", profile.learner_uuid),
    ):
        await authorize_studytomy_staff_action(
            db,
            actor,
            org,
            StudytomyCapabilityName.ASSESSMENT_MODERATION,
            target_scope_kind=kind,
            target_resource_uuid=uuid,
            subject_user_id=profile.user_id,
            hide_existence_on_denial=True,
            now=now,
        )
    policies = await _current_policies(
        db,
        org,
        (
            StudyPolicyKey.D_01,
            StudyPolicyKey.D_02,
            StudyPolicyKey.D_08,
            StudyPolicyKey.D_09,
            StudyPolicyKey.D_12,
            StudyPolicyKey.D_13,
        ),
        now,
    )
    d09 = next(
        policy for policy in policies if policy.policy_key == StudyPolicyKey.D_09.value
    )
    policy = resolve_mastery_policy(
        StudyPolicyBody.model_validate(d09.body), policy_uuid
    )
    row, watermark, result = await _mastery_snapshot(db, profile, concept, policy, now)
    if row is not None and _matches(row, watermark, result):
        return MasteryIntegrity("current", watermark, result, row.version)
    if not repair:
        return MasteryIntegrity(
            "missing" if row is None else "mismatch",
            watermark,
            result,
            row.version if row else None,
        )
    row = await _store_projection(
        db, profile, concept, policy, row, watermark, result, now
    )
    await append_business_event(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        subject_uuid=learner_uuid,
        resource_kind="concept_mastery",
        resource_uuid=row.uuid,
        action="studytomy.adaptive.mastery.repaired",
        reason_code="EVIDENCE_PROJECTION_REPAIR",
        after_state={"version": row.version, "status": row.state},
        policy_refs=[int(record.id) for record in policies],
    )
    return MasteryIntegrity("repaired", watermark, result, row.version)
