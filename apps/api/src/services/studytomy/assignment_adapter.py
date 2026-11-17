"""Resolve the reviewed snapshot for an explicitly opted-in native assignment task."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import Activity
from src.db.courses.assignments import (
    Assignment,
    AssignmentTask,
    AssignmentTaskSubmission,
    AssignmentUserSubmission,
    AssignmentUserSubmissionStatus,
)
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.adaptive import (
    StudyConcept,
    StudyConceptActivity,
    StudyCurriculumRevision,
)
from src.db.studytomy.adaptive_evidence import (
    StudyAttemptItem,
    StudyLearningAttempt,
    StudyLearningEvidence,
)
from src.db.studytomy.adaptive_items import StudyItemRevision
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyPolicyBody,
    StudyPolicyKey,
    StudyPolicyVersion,
)
from src.db.studytomy.privacy import StudyConsentPurpose
from src.db.users import PublicUser
from src.security.session_context import POLICY_AUTH_METHODS, get_session_provenance
from src.services.studytomy.adaptive_curriculum import curriculum_activity_digest
from src.services.studytomy.adaptive_curriculum_review import (
    _REVIEW_POLICIES,
    CurriculumPublish,
)
from src.services.studytomy.adaptive_diagnostics import _policy_body
from src.services.studytomy.adaptive_evidence import append_evidence_and_project
from src.services.studytomy.adaptive_item_review import (
    _require_current_native_source,
    _require_item_and_curriculum_approval,
)
from src.services.studytomy.adaptive_mastery import PolicyUUID, resolve_mastery_policy
from src.services.studytomy.adaptive_objective_scoring import (
    InvalidObjectiveResponse,
    evaluate_objective_item,
)
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.consents import has_current_consent_scope
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization


class NativeAssignmentOptIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    assignment_uuid: str
    mapping_uuid: str
    item_uuid: str
    item_digest: str
    assistance: Literal["independent_only", "supported"]
    mastery_policy_uuid: PolicyUUID | None = None


@dataclass(frozen=True, slots=True)
class NativeAssignmentSnapshot:
    mapping_id: int
    item_revision_id: int
    curriculum_revision_id: int
    concept_id: int
    policy_version_ids: tuple[int, ...]
    manifest_digest: str
    assistance: Literal["independent_only", "supported"]
    mastery_policy_uuid: str | None
    mastery_policy_digest: str | None


def native_round_uuid(org_id: int, submission_uuid: str, attempt_number: int) -> str:
    """An incremented or recreated native submission always has a separate round."""
    if org_id < 1 or not submission_uuid or attempt_number < 1:
        raise ValueError("A native round requires a persisted submission identity.")
    digest = canonical_request_hash([org_id, submission_uuid, attempt_number])
    return f"round_{digest[:58]}"


async def resolve_native_assignment_snapshot(
    db: AsyncSession,
    org: Organization,
    task: AssignmentTask,
    now: datetime,
    *,
    question_uuid: str | None = None,
) -> NativeAssignmentSnapshot | None:
    """Resolve only current exact mappings. Missing history stays unverified."""
    rows = (
        await db.execute(
            select(
                StudyItemRevision,
                StudyConceptActivity,
                StudyCurriculumRevision,
                Activity,
                Assignment,
            )
            .select_from(StudyItemRevision)
            .join(
                Assignment,
                Assignment.assignment_uuid == StudyItemRevision.assignment_uuid,
            )
            .join(Activity, Activity.id == Assignment.activity_id)
            .join(
                StudyCurriculumRevision,
                StudyCurriculumRevision.id == StudyItemRevision.curriculum_revision_id,
            )
            .join(
                StudyConceptActivity,
                StudyConceptActivity.concept_id == StudyItemRevision.concept_id,
            )
            .join(StudyConcept, StudyConcept.id == StudyConceptActivity.concept_id)
            .where(
                StudyItemRevision.org_id == org.id,
                StudyItemRevision.status == "published",
                StudyItemRevision.source_task_uuid == task.assignment_task_uuid,
                StudyItemRevision.source_question_uuid == question_uuid,
                Assignment.id == task.assignment_id,
                Assignment.org_id == org.id,
                Activity.org_id == org.id,
                Activity.published.is_(True),
                StudyCurriculumRevision.org_id == org.id,
                StudyCurriculumRevision.status == "published",
                StudyCurriculumRevision.owner_course_uuid
                == StudyItemRevision.course_uuid,
                StudyConceptActivity.org_id == org.id,
                StudyConceptActivity.curriculum_revision_id
                == StudyItemRevision.curriculum_revision_id,
                StudyConceptActivity.activity_id == Activity.id,
                StudyConceptActivity.activity_uuid == Activity.activity_uuid,
                StudyConceptActivity.course_id == Assignment.course_id,
                StudyConceptActivity.course_uuid == StudyItemRevision.course_uuid,
                StudyConceptActivity.activity_version == Activity.current_version,
                StudyConceptActivity.usage == "practice",
                StudyConceptActivity.archived.is_(False),
                StudyConcept.org_id == org.id,
                StudyConcept.archived.is_(False),
            )
            .with_for_update(
                read=True,
                of=(StudyItemRevision, StudyConceptActivity, StudyCurriculumRevision),
            )
        )
    ).all()
    if not rows:
        return None
    try:
        policies = await _current_policies(
            db, org, (*_REVIEW_POLICIES, StudyPolicyKey.D_08), now
        )
    except HTTPException as error:
        if error.status_code == 409:
            return None
        raise
    raw_attempts = _policy_body(policies, StudyPolicyKey.D_08).specified_value(
        "attempts"
    )
    raw_entries = (
        raw_attempts.get("adaptive_native_assignments")
        if isinstance(raw_attempts, dict)
        else None
    )
    if not isinstance(raw_entries, list):
        return None
    try:
        entries = [NativeAssignmentOptIn.model_validate(entry) for entry in raw_entries]
    except ValidationError:
        return None
    approval = _policy_body(policies, StudyPolicyKey.D_16).specified_value(
        "review_approvals"
    )
    reviews = (
        approval.get("adaptive_item_reviews") if isinstance(approval, dict) else None
    )
    if not isinstance(reviews, list):
        return None
    matches = []
    for item, mapping, curriculum, activity, assignment in rows:
        accepted = [
            entry
            for entry in entries
            if (
                entry.assignment_uuid,
                entry.mapping_uuid,
                entry.item_uuid,
                entry.item_digest,
            )
            == (
                assignment.assignment_uuid,
                mapping.uuid,
                item.uuid,
                item.content_digest,
            )
        ]
        if len(accepted) != 1 or mapping.activity_digest != curriculum_activity_digest(
            activity
        ):
            continue
        for review in reviews:
            if (
                not isinstance(review, dict)
                or review.get("revision_uuid") != item.uuid
                or review.get("digest") != item.content_digest
            ):
                continue
            try:
                policy_uuid = accepted[0].mastery_policy_uuid
                mastery = (
                    resolve_mastery_policy(
                        _policy_body(policies, StudyPolicyKey.D_09), policy_uuid
                    )
                    if policy_uuid
                    else None
                )
                command = CurriculumPublish(
                    expected_version=item.version,
                    expected_digest=item.content_digest,
                    review_evidence=review.get("evidence_reference"),
                )
                _require_item_and_curriculum_approval(
                    policies, item, curriculum, command
                )
                if (
                    await _require_current_native_source(db, org, item)
                    != activity.activity_uuid
                ):
                    continue
            except ValidationError:
                continue
            except HTTPException as error:
                if error.status_code == 409:
                    continue
                raise
            ids = tuple(int(policy.id) for policy in policies)
            matches.append(
                NativeAssignmentSnapshot(
                    mapping.id,
                    item.id,
                    curriculum.id,
                    item.concept_id,
                    ids,
                    canonical_request_hash(
                        {
                            "opt_in": accepted[0].model_dump(
                                mode="json", exclude_none=True
                            ),
                            "curriculum_digest": curriculum.content_digest,
                            "activity_digest": mapping.activity_digest,
                            "policy_version_ids": ids,
                            **(
                                {"mastery_policy_digest": mastery.digest}
                                if mastery
                                else {}
                            ),
                        }
                    ),
                    accepted[0].assistance,
                    policy_uuid,
                    mastery.digest if mastery else None,
                )
            )
    return matches[0] if len(matches) == 1 else None


async def capture_native_assignment_exposure(
    db: AsyncSession,
    assignment: Assignment,
    actor: PublicUser,
) -> bool:
    """Journal a native exposure after the caller has enforced native read rules."""
    provenance = get_session_provenance()
    if provenance is None or provenance.amr not in POLICY_AUTH_METHODS:
        return False
    rows = (
        await db.execute(
            select(AssignmentTask, StudyItemRevision.source_question_uuid)
            .join(
                StudyItemRevision,
                StudyItemRevision.source_task_uuid
                == AssignmentTask.assignment_task_uuid,
            )
            .where(
                AssignmentTask.assignment_id == assignment.id,
                StudyItemRevision.org_id == assignment.org_id,
                StudyItemRevision.assignment_uuid == assignment.assignment_uuid,
                StudyItemRevision.status == "published",
            )
            .order_by(AssignmentTask.id, StudyItemRevision.id)
        )
    ).all()
    if not rows:
        return False

    # Native assignment routes import the adapter lazily. Shared attempt access
    # also uses the native assignment reader gate for supported practice.
    from src.services.studytomy.adaptive_attempt_access import lock_attempt_learner

    org = await lock_organization(db, assignment.org_id)
    now = datetime.now(UTC)
    try:
        learner = await lock_attempt_learner(
            db, org, actor, now, "adaptive_native_assignments"
        )
    except HTTPException as error:
        if error.status_code in {403, 404}:
            return False
        raise

    native_query = (
        select(AssignmentUserSubmission)
        .where(
            AssignmentUserSubmission.assignment_id == assignment.id,
            AssignmentUserSubmission.user_id == actor.id,
        )
        .with_for_update()
    )
    native = (await db.execute(native_query)).scalar_one_or_none()
    pending = {
        AssignmentUserSubmissionStatus.PENDING,
        AssignmentUserSubmissionStatus.NOT_SUBMITTED,
    }
    if native and native.submission_status not in pending:
        return False
    if native:
        exists = (
            await db.execute(
                select(StudyLearningAttempt.id).where(
                    StudyLearningAttempt.org_id == org.id,
                    StudyLearningAttempt.source_round_uuid
                    == native_round_uuid(
                        org.id,
                        native.assignmentusersubmission_uuid,
                        native.attempt_number,
                    ),
                )
            )
        ).scalar_one_or_none()
        if exists is not None:
            return False
    previously_saved = (
        await db.execute(
            select(AssignmentTaskSubmission.id)
            .join(
                AssignmentTask,
                AssignmentTask.id == AssignmentTaskSubmission.assignment_task_id,
            )
            .where(
                AssignmentTask.assignment_id == assignment.id,
                AssignmentTaskSubmission.user_id == actor.id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if previously_saved is not None:
        return False

    snapshots = []
    seen = set()
    for task, question_uuid in rows:
        key = (task.id, question_uuid)
        if key in seen:
            continue
        seen.add(key)
        snapshot = await resolve_native_assignment_snapshot(
            db, org, task, now, question_uuid=question_uuid
        )
        if snapshot is not None:
            snapshots.append(snapshot)
    if not snapshots or len({entry.curriculum_revision_id for entry in snapshots}) != 1:
        return False
    activity = await db.get(Activity, assignment.activity_id)
    curriculum = await db.get(
        StudyCurriculumRevision, snapshots[0].curriculum_revision_id
    )
    if activity is None or curriculum is None:
        return False
    if native is None:
        native = AssignmentUserSubmission(
            assignment_id=assignment.id,
            user_id=actor.id,
            grade=0,
            attempt_number=1,
            assignmentusersubmission_uuid=f"assignmentusersubmission_{uuid4()}",
            submission_status=AssignmentUserSubmissionStatus.PENDING,
            creation_date=now.isoformat(),
            update_date=now.isoformat(),
        )
        try:
            async with db.begin_nested():
                db.add(native)
                await db.flush()
        except IntegrityError:
            # A concurrent ordinary native submit may have created the row.
            # Its earlier exposure cannot safely be inferred from this read.
            if (await db.execute(native_query)).scalar_one_or_none() is None:
                raise
            return False

    course = await db.get(Course, assignment.course_id)
    if course is not None and course.studytomy_managed is True and not assignment.ungraded:
        from src.services.studytomy.grading import record_grading_contract

        await record_grading_contract(db, assignment, native, actor, issued=True)

    attempt = StudyLearningAttempt(
        org_id=org.id,
        learner_profile_id=learner.id,
        actor_user_id=actor.id,
        actor_capacity="learner",
        kind="assignment",
        curriculum_revision_id=curriculum.id,
        source_course_uuid=curriculum.owner_course_uuid,
        source_activity_uuid=activity.activity_uuid,
        source_round_uuid=native_round_uuid(
            org.id, native.assignmentusersubmission_uuid, native.attempt_number
        ),
        source_assignment_uuid=assignment.assignment_uuid,
        source_submission_uuid=native.assignmentusersubmission_uuid,
        source_attempt_number=native.attempt_number,
        started_at=now,
        expires_at=datetime.max.replace(tzinfo=UTC),
        manifest_digest=canonical_request_hash(
            [entry.manifest_digest for entry in snapshots]
        ),
        policy_version_ids=list(snapshots[0].policy_version_ids),
    )
    db.add(attempt)
    await db.flush()
    for snapshot in snapshots:
        item = await db.get(StudyItemRevision, snapshot.item_revision_id)
        issued = StudyAttemptItem(
            org_id=org.id,
            attempt_id=attempt.id,
            learner_profile_id=learner.id,
            curriculum_revision_id=curriculum.id,
            concept_id=snapshot.concept_id,
            item_revision_id=item.id,
            item_family_uuid=item.item_family_uuid,
            ordinal=attempt.next_ordinal,
            is_diagnostic=False,
            issued_at=now,
        )
        db.add(issued)
        await db.flush()
        attempt.next_ordinal += 1
        attempt.version += 1
        await append_business_event(
            db,
            org_id=org.id,
            actor_user_id=actor.id,
            actor_capacity="learner",
            action="studytomy.adaptive.native_assignment.exposed",
            reason_code="NATIVE_SOURCE_REVIEWED",
            resource_kind="assignment_attempt_item",
            resource_uuid=issued.uuid,
            after_state={
                "source_mapping_id": snapshot.mapping_id,
                "source_snapshot_digest": snapshot.manifest_digest,
                "assistance_rule": snapshot.assistance,
                "mastery_policy_uuid": snapshot.mastery_policy_uuid,
                "mastery_policy_digest": snapshot.mastery_policy_digest,
            },
            policy_refs=list(snapshot.policy_version_ids),
        )
        await db.flush()
    return True


def _native_objective_response(item: StudyItemRevision, saved: dict) -> dict | None:
    if item.kind == "number":
        answer = saved.get("answer")
        if type(answer) in (int, float, str) and str(answer).strip():
            return {"kind": "number", "value": str(answer).strip()}
        return None
    answers = saved.get("submissions")
    if not isinstance(answers, list):
        return None
    selected = {}
    for row in answers:
        if (
            isinstance(row, dict)
            and row.get("questionUUID") == item.source_question_uuid
            and isinstance(row.get("optionUUID"), str)
        ):
            if not isinstance(row.get("answer"), bool):
                return None
            selected[row["optionUUID"]] = row["answer"]
    choices = [uuid for uuid, active in selected.items() if active]
    if len(choices) == 1:
        return {"kind": "single_choice", "option_uuid": choices[0]}
    return None


async def freeze_native_assignment_answers(
    db: AsyncSession,
    assignment: Assignment,
    native: AssignmentUserSubmission,
    *,
    direct_learner: bool,
) -> None:
    """Freeze the captured round in the existing native hand-in transaction."""
    attempt = (
        await db.execute(
            select(StudyLearningAttempt)
            .where(
                StudyLearningAttempt.org_id == assignment.org_id,
                StudyLearningAttempt.source_round_uuid
                == native_round_uuid(
                    assignment.org_id,
                    native.assignmentusersubmission_uuid,
                    native.attempt_number,
                ),
                StudyLearningAttempt.source_assignment_uuid
                == assignment.assignment_uuid,
                StudyLearningAttempt.actor_user_id == native.user_id,
                StudyLearningAttempt.status == "active",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if attempt is None:
        return
    provenance = get_session_provenance()
    direct_learner = (
        direct_learner
        and provenance is not None
        and provenance.amr in POLICY_AUTH_METHODS
    )
    now = datetime.now(UTC)
    org = await db.get(Organization, assignment.org_id)
    learner = await db.get(StudyLearnerProfile, attempt.learner_profile_id)
    consent = direct_learner and await has_current_consent_scope(
        db,
        org,
        learner,
        StudyConsentPurpose.LEARNING_TERMS,
        "adaptive_native_assignments",
        now,
    )
    rows = (
        await db.execute(
            select(StudyAttemptItem, StudyItemRevision, StudyBusinessEvent.after_state)
            .select_from(StudyAttemptItem)
            .join(
                StudyItemRevision,
                StudyItemRevision.id == StudyAttemptItem.item_revision_id,
            )
            .join(
                StudyBusinessEvent,
                StudyBusinessEvent.resource_uuid == StudyAttemptItem.uuid,
            )
            .where(
                StudyAttemptItem.org_id == org.id,
                StudyAttemptItem.attempt_id == attempt.id,
                StudyAttemptItem.response_state == "issued",
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.action
                == "studytomy.adaptive.native_assignment.exposed",
            )
            .order_by(StudyAttemptItem.ordinal)
            .with_for_update(of=StudyAttemptItem)
        )
    ).all()
    if len(rows) != attempt.next_ordinal - 1:
        raise HTTPException(
            409, "The retained assignment round is incomplete. Please retry hand-in."
        )
    answers = {}
    if consent:
        stored = (
            await db.execute(
                select(AssignmentTask, AssignmentTaskSubmission)
                .join(
                    AssignmentTaskSubmission,
                    AssignmentTaskSubmission.assignment_task_id == AssignmentTask.id,
                )
                .where(
                    AssignmentTask.assignment_id == assignment.id,
                    AssignmentTaskSubmission.user_id == native.user_id,
                )
                .with_for_update(of=AssignmentTaskSubmission)
            )
        ).all()
        answers = {
            task.assignment_task_uuid: (task, submission.task_submission)
            for task, submission in stored
        }
    for issued, item, source in rows:
        reason = "delegated_hand_in" if not direct_learner else "consent_unavailable"
        normalized = None
        if consent:
            reason = "native_answer_missing"
            if item.source_task_uuid in answers:
                task, answer = answers[item.source_task_uuid]
                snapshot = await resolve_native_assignment_snapshot(
                    db,
                    org,
                    task,
                    now,
                    question_uuid=item.source_question_uuid,
                )
                reason = "source_revision_unverified"
                if (
                    snapshot
                    and source
                    and snapshot.manifest_digest == source.get("source_snapshot_digest")
                ):
                    reason = "native_answer_unverified"
                    response = _native_objective_response(item, answer)
                    if response:
                        try:
                            normalized = evaluate_objective_item(
                                item, response
                            ).normalized_response
                        except InvalidObjectiveResponse:
                            pass
        issued.response_version += 1
        issued.responded_at = now
        if normalized is None:
            issued.response_state = "skipped"
            issued.skip_reason = reason
        else:
            issued.response_state = "answered"
            issued.normalized_response = normalized
            issued.response_digest = canonical_request_hash(
                {"item_uuid": issued.uuid, "normalized_response": normalized}
            )
            # Current native answer rows cannot distinguish a self-written
            # answer from earlier delegated saves. Preserve it without
            # allowing the result to become independent evidence.
            issued.exposure_count += 1
    await db.flush()
    attempt.status = "completed"
    attempt.ended_at = now
    attempt.version += 1
    await db.flush()


async def journal_native_assignment_grade(
    db: AsyncSession,
    assignment: Assignment,
    native: AssignmentUserSubmission,
    manual_task_uuids: frozenset[str],
) -> None:
    """Append ineligible item outcomes within the authoritative native grade commit."""
    if not native.assignmentusersubmission_uuid or not native.attempt_number:
        return
    with db.no_autoflush:
        attempt = (
            await db.execute(
                select(StudyLearningAttempt).where(
                    StudyLearningAttempt.org_id == assignment.org_id,
                    StudyLearningAttempt.kind == "assignment",
                    StudyLearningAttempt.status == "completed",
                    StudyLearningAttempt.source_round_uuid
                    == native_round_uuid(
                        assignment.org_id,
                        native.assignmentusersubmission_uuid,
                        native.attempt_number,
                    ),
                    StudyLearningAttempt.source_assignment_uuid
                    == assignment.assignment_uuid,
                    StudyLearningAttempt.actor_user_id == native.user_id,
                )
            )
        ).scalar_one_or_none()
        if attempt is None:
            return
        await db.execute(
            select(StudyLearnerProfile.id)
            .where(
                StudyLearnerProfile.org_id == attempt.org_id,
                StudyLearnerProfile.id == attempt.learner_profile_id,
            )
            .with_for_update()
        )
        org = await db.get(Organization, attempt.org_id)
        learner = await db.get(StudyLearnerProfile, attempt.learner_profile_id)
        if not await has_current_consent_scope(
            db,
            org,
            learner,
            StudyConsentPurpose.LEARNING_TERMS,
            "adaptive_native_assignments",
        ):
            return
    sources = (
        await db.execute(
            select(StudyAttemptItem, StudyItemRevision)
            .join(
                StudyItemRevision,
                StudyItemRevision.id == StudyAttemptItem.item_revision_id,
            )
            .where(
                StudyAttemptItem.org_id == attempt.org_id,
                StudyAttemptItem.attempt_id == attempt.id,
            )
            .order_by(StudyAttemptItem.ordinal)
        )
    ).all()
    for issued, item in sources:
        outcome = "not_scored"
        reason = issued.skip_reason or "native_answer_unverified"
        if item.source_task_uuid in manual_task_uuids:
            reason = "native_manual_override"
        elif issued.response_state == "answered":
            try:
                result = evaluate_objective_item(
                    item,
                    {
                        "kind": item.kind,
                        (
                            "value" if item.kind == "number" else "option_uuid"
                        ): issued.normalized_response,
                    },
                )
            except InvalidObjectiveResponse:
                pass
            else:
                outcome = result.outcome
        previous = (
            await db.execute(
                select(StudyLearningEvidence)
                .where(
                    StudyLearningEvidence.org_id == attempt.org_id,
                    StudyLearningEvidence.logical_source_uuid == issued.uuid,
                )
                .order_by(StudyLearningEvidence.result_revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if previous and (
            previous.is_retraction
            or (previous.outcome, previous.eligibility_reason) == (outcome, reason)
        ):
            continue
        evidence = StudyLearningEvidence(
            org_id=attempt.org_id,
            learner_profile_id=attempt.learner_profile_id,
            curriculum_revision_id=attempt.curriculum_revision_id,
            concept_id=issued.concept_id,
            attempt_item_id=issued.id,
            logical_source_uuid=issued.uuid,
            evaluator_version=item.evaluator_version,
            policy_version_ids=attempt.policy_version_ids,
            outcome=outcome,
            score={"correct": 1, "incorrect": 0, "not_scored": None}[outcome],
            independence="unknown",
            eligible=False,
            eligibility_reason=reason,
            observed_at=issued.responded_at,
            actor_reference="native_assignment_grader",
            result_revision=previous.result_revision + 1 if previous else 1,
            supersedes_id=previous.id if previous else None,
            correction_reason="native_grade_recomputed" if previous else None,
        )
        await _append_native_evidence(db, evidence)


async def _append_native_evidence(
    db: AsyncSession, evidence: StudyLearningEvidence
) -> None:
    exposure = (
        await db.execute(
            select(StudyBusinessEvent.after_state).where(
                StudyBusinessEvent.org_id == evidence.org_id,
                StudyBusinessEvent.resource_uuid == evidence.logical_source_uuid,
                StudyBusinessEvent.action
                == "studytomy.adaptive.native_assignment.exposed",
            )
        )
    ).scalar_one_or_none()
    if exposure and exposure.get("mastery_policy_uuid"):
        version = (
            await db.execute(
                select(StudyPolicyVersion).where(
                    StudyPolicyVersion.org_id == evidence.org_id,
                    StudyPolicyVersion.id.in_(evidence.policy_version_ids),
                    StudyPolicyVersion.policy_key == StudyPolicyKey.D_09.value,
                )
            )
        ).scalar_one_or_none()
        if version is None:
            raise HTTPException(409, "The retained mastery policy is unavailable.")
        policy = resolve_mastery_policy(
            StudyPolicyBody.model_validate(version.body),
            exposure["mastery_policy_uuid"],
        )
        if policy.digest != exposure.get("mastery_policy_digest"):
            raise HTTPException(
                409, "The retained mastery policy does not match the reviewed round."
            )
        learner = await db.get(StudyLearnerProfile, evidence.learner_profile_id)
        concept = await db.get(StudyConcept, evidence.concept_id)
        await append_evidence_and_project(db, learner, concept, evidence, policy)
        return
    if db.get_bind().dialect.name == "sqlite":
        watermark = (
            await db.execute(
                select(func.max(StudyLearningEvidence.evidence_sequence)).where(
                    StudyLearningEvidence.org_id == evidence.org_id,
                    StudyLearningEvidence.learner_profile_id
                    == evidence.learner_profile_id,
                )
            )
        ).scalar_one()
        evidence.evidence_sequence = (watermark or 0) + 1
    db.add(evidence)
    await db.flush()


async def ensure_native_source_deletable(
    db: AsyncSession,
    org_id: int,
    scope: Literal["assignment", "task", "activity", "course"],
    resource_uuid: str,
) -> None:
    """Protect open adaptive rounds and retained managed assignments."""
    await lock_organization(db, org_id)
    managed_assignments = (
        select(Assignment.id)
        .join(Course, Course.id == Assignment.course_id)
        .where(
            Assignment.org_id == org_id,
            Course.org_id == org_id,
            Course.studytomy_managed.is_(True),
        )
    )
    if scope == "task":
        managed_assignments = managed_assignments.join(
            AssignmentTask, AssignmentTask.assignment_id == Assignment.id
        ).where(AssignmentTask.assignment_task_uuid == resource_uuid)
    elif scope == "activity":
        managed_assignments = managed_assignments.join(
            Activity, Activity.id == Assignment.activity_id
        ).where(Activity.activity_uuid == resource_uuid)
    elif scope == "course":
        managed_assignments = managed_assignments.where(Course.course_uuid == resource_uuid)
    else:
        managed_assignments = managed_assignments.where(Assignment.assignment_uuid == resource_uuid)

    native_round = await db.scalar(
        select(AssignmentUserSubmission.id)
        .where(AssignmentUserSubmission.assignment_id.in_(managed_assignments))
        .limit(1)
    )
    native_response = await db.scalar(
        select(AssignmentTaskSubmission.id)
        .join(AssignmentTask, AssignmentTask.id == AssignmentTaskSubmission.assignment_task_id)
        .where(AssignmentTask.assignment_id.in_(managed_assignments))
        .limit(1)
    ) if native_round is None else None
    if native_round is not None or native_response is not None:
        raise HTTPException(
            409,
            detail={
                "code": "MANAGED_ASSESSMENT_SOURCE_RETAINED",
                "message": "This source has retained managed assessment attempts.",
            },
        )

    query = select(StudyLearningAttempt.id).where(
        StudyLearningAttempt.org_id == org_id,
        StudyLearningAttempt.kind == "assignment",
        StudyLearningAttempt.status == "active",
    )
    if scope == "task":
        query = (
            query.join(
                StudyAttemptItem,
                (StudyAttemptItem.org_id == StudyLearningAttempt.org_id)
                & (StudyAttemptItem.attempt_id == StudyLearningAttempt.id),
            )
            .join(
                StudyItemRevision,
                (StudyItemRevision.org_id == StudyAttemptItem.org_id)
                & (StudyItemRevision.id == StudyAttemptItem.item_revision_id),
            )
            .where(StudyItemRevision.source_task_uuid == resource_uuid)
        )
    else:
        column = {
            "assignment": StudyLearningAttempt.source_assignment_uuid,
            "activity": StudyLearningAttempt.source_activity_uuid,
            "course": StudyLearningAttempt.source_course_uuid,
        }[scope]
        query = query.where(column == resource_uuid)
    if (await db.execute(query.limit(1))).scalar_one_or_none() is not None:
        raise HTTPException(
            409,
            detail={
                "code": "ADAPTIVE_SOURCE_IN_USE",
                "message": "This source has an open adaptive assignment round. Finish or reject the round before deleting it.",
            },
        )


async def ensure_native_retry_round_frozen(
    db: AsyncSession,
    assignment: Assignment,
    native: AssignmentUserSubmission,
) -> None:
    """Never clear native answers while the matching adaptive round is open."""
    if not native.assignmentusersubmission_uuid or not native.attempt_number:
        return
    state = (
        await db.execute(
            select(StudyLearningAttempt.status).where(
                StudyLearningAttempt.org_id == assignment.org_id,
                StudyLearningAttempt.kind == "assignment",
                StudyLearningAttempt.source_assignment_uuid
                == assignment.assignment_uuid,
                StudyLearningAttempt.source_round_uuid
                == native_round_uuid(
                    assignment.org_id,
                    native.assignmentusersubmission_uuid,
                    native.attempt_number,
                ),
                StudyLearningAttempt.actor_user_id == native.user_id,
            )
        )
    ).scalar_one_or_none()
    if state == "active":
        raise HTTPException(
            409,
            detail={
                "code": "ADAPTIVE_ROUND_NOT_FROZEN",
                "message": "The assignment answers are still being retained. Ask a teacher to review this hand-in before retrying.",
            },
        )


async def retract_rejected_native_round(
    db: AsyncSession,
    assignment: Assignment,
    native: AssignmentUserSubmission,
    actor: PublicUser,
) -> None:
    """Invalidate this round without erasing accepted responses or prior results."""
    if not native.assignmentusersubmission_uuid or not native.attempt_number:
        return
    source_round = native_round_uuid(
        assignment.org_id, native.assignmentusersubmission_uuid, native.attempt_number
    )
    attempt = (
        await db.execute(
            select(StudyLearningAttempt).where(
                StudyLearningAttempt.org_id == assignment.org_id,
                StudyLearningAttempt.kind == "assignment",
                StudyLearningAttempt.source_assignment_uuid
                == assignment.assignment_uuid,
                StudyLearningAttempt.source_round_uuid == source_round,
                StudyLearningAttempt.actor_user_id == native.user_id,
            )
        )
    ).scalar_one_or_none()
    if attempt is None:
        return
    await db.execute(
        select(StudyLearnerProfile.id)
        .where(
            StudyLearnerProfile.org_id == attempt.org_id,
            StudyLearnerProfile.id == attempt.learner_profile_id,
        )
        .with_for_update()
    )
    locked_native = (
        await db.execute(
            select(AssignmentUserSubmission)
            .where(
                AssignmentUserSubmission.id == native.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if locked_native is None or source_round != native_round_uuid(
        assignment.org_id,
        locked_native.assignmentusersubmission_uuid,
        locked_native.attempt_number,
    ):
        raise HTTPException(
            409, "The assignment round changed. Reload before rejecting it."
        )
    await db.refresh(attempt, with_for_update=True)
    if attempt.status == "active":
        pending = (
            (
                await db.execute(
                    select(StudyAttemptItem)
                    .where(
                        StudyAttemptItem.org_id == attempt.org_id,
                        StudyAttemptItem.attempt_id == attempt.id,
                        StudyAttemptItem.response_state == "issued",
                    )
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        now = datetime.now(UTC)
        for item in pending:
            item.response_state = "skipped"
            item.skip_reason = "native_submission_rejected"
            item.responded_at = now
            item.response_version += 1
        await db.flush()
        attempt.status = "invalidated"
        attempt.ended_at = now
        attempt.version += 1
        await db.flush()
    source_ids = select(StudyAttemptItem.id).where(
        StudyAttemptItem.org_id == attempt.org_id,
        StudyAttemptItem.attempt_id == attempt.id,
    )
    revisions_query = (
        select(StudyLearningEvidence)
        .where(
            StudyLearningEvidence.org_id == attempt.org_id,
            StudyLearningEvidence.learner_profile_id == attempt.learner_profile_id,
            StudyLearningEvidence.attempt_item_id.in_(source_ids),
        )
        .order_by(
            StudyLearningEvidence.logical_source_uuid,
            StudyLearningEvidence.result_revision.desc(),
        )
    )
    if db.get_bind().dialect.name == "postgresql":
        revisions_query = revisions_query.distinct(
            StudyLearningEvidence.logical_source_uuid
        )
    revisions = (await db.execute(revisions_query)).scalars().all()

    latest = {}
    for row in revisions:
        latest.setdefault(row.logical_source_uuid, row)
    for previous in latest.values():
        if previous.is_retraction:
            continue
        await _append_native_evidence(
            db,
            StudyLearningEvidence(
                org_id=previous.org_id,
                learner_profile_id=previous.learner_profile_id,
                curriculum_revision_id=previous.curriculum_revision_id,
                concept_id=previous.concept_id,
                attempt_item_id=previous.attempt_item_id,
                logical_source_uuid=previous.logical_source_uuid,
                evaluator_version=previous.evaluator_version,
                policy_version_ids=previous.policy_version_ids,
                outcome="not_scored",
                independence="unknown",
                eligible=False,
                eligibility_reason="native_submission_rejected",
                observed_at=previous.observed_at,
                actor_user_id=actor.id,
                result_revision=previous.result_revision + 1,
                supersedes_id=previous.id,
                correction_reason="native_submission_rejected",
                is_retraction=True,
            ),
        )
