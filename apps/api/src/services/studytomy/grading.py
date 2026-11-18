"""Capture the grading contract for a managed native assessment round."""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.assignments import (
    Assignment,
    AssignmentTask,
    AssignmentTaskSubmission,
    AssignmentUserSubmission,
    AssignmentUserSubmissionStatus,
)
from src.db.organizations import Organization
from src.db.studytomy.assessment_archive import StudyAssessmentResponseArchive
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyPolicyBody,
    StudyPolicyKey,
)
from src.db.users import APITokenUser, PublicUser
from src.services.studytomy.assignment_adapter import native_round_uuid
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.grading_contract import (
    NATIVE_GRADING_CONTRACT_VERSION,
    native_grading_digest,
    retained_grading_contract_digest,
)
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization

CONTRACT_FROZEN_ACTION = "studytomy.assessment.contract_frozen"
RESULT_RECORDED_ACTION = "studytomy.assessment.result_recorded"
APPEAL_CORRECTION_REASON = "ASSESSMENT_APPEAL_SCORE_CORRECTED"
RESPONSES_FROZEN_ACTION = "studytomy.assessment.responses_frozen"


def grading_contract_digest(
    assignment: Assignment, tasks: Sequence[AssignmentTask]
) -> str:
    return native_grading_digest(
        assignment.assignment_uuid, assignment.grading_type,
        assignment.pass_threshold_percentage, bool(assignment.ungraded),
        ((task.assignment_task_uuid, task.assignment_type, task.max_grade_value, task.contents)
         for task in tasks),
    )


async def _assessed_release_binding(
    db: AsyncSession, assignment: Assignment, actor: PublicUser, digest: str,
) -> dict[str, str]:
    from src.services.studytomy.access_policy import resolve_single_started_assessment_release

    org = await db.get(Organization, assignment.org_id)
    if org is None:
        return {}
    now = datetime.now(UTC)
    release = await resolve_single_started_assessment_release(db, org, actor, assignment.course_id, now)
    if release is None:
        return {}
    try:
        (assessment_policy,) = await _current_policies(db, org, (StudyPolicyKey.D_08,), now)
        if assessment_policy.id != release.assessment_policy_id:
            return {}
        retained = [
            row for row in release.snapshot["assignments"]
            if row["assignment_uuid"] == assignment.assignment_uuid
            and row["id"] == assignment.id
            and row["activity_id"] == assignment.activity_id
            and row["published"] is True
        ]
        published_activity = any(
            row["id"] == assignment.activity_id and row["published"] is True
            and row["activity_sub_type"] == "SUBTYPE_ASSIGNMENT_ANY"
            for row in release.snapshot["activities"]
        )
        if not published_activity or len(retained) != 1 or retained_grading_contract_digest(
            retained[0], release.snapshot["assignment_tasks"],
        ) != digest:
            return {}
    except HTTPException as error:
        if error.status_code != 409:
            raise
        return {}
    except (KeyError, TypeError, ValueError):
        return {}
    return {"assessment_release_uuid": release.uuid, "assessment_release_sha256": release.snapshot_sha256}


async def record_grading_contract(
    db: AsyncSession,
    assignment: Assignment,
    submission: AssignmentUserSubmission,
    actor: PublicUser | APITokenUser,
    *,
    issued: bool = False,
) -> bool:
    round_uuid = native_round_uuid(
        assignment.org_id,
        submission.assignmentusersubmission_uuid,
        submission.attempt_number,
    )
    dedupe_key = f"assessment_contract:{round_uuid}"
    existing = await db.scalar(
        select(StudyBusinessEvent.id).where(
            StudyBusinessEvent.dedupe_key == dedupe_key,
            StudyBusinessEvent.org_id == assignment.org_id,
        )
    )
    if existing is not None:
        return False
    tasks = (
        (
            await db.execute(
                select(AssignmentTask).where(
                    AssignmentTask.assignment_id == assignment.id
                )
            )
        )
        .scalars()
        .all()
    )
    token = isinstance(actor, APITokenUser)
    digest = grading_contract_digest(assignment, tasks)
    release_binding = (
        await _assessed_release_binding(db, assignment, actor, digest)
        if issued and isinstance(actor, PublicUser) and actor.id == submission.user_id
        else {}
    )
    await append_business_event(
        db,
        org_id=assignment.org_id,
        actor_user_id=None if token else actor.id,
        actor_machine_id=f"assignment_token_{actor.id}" if token else None,
        actor_capacity="api_token" if token else "session_user",
        action=CONTRACT_FROZEN_ACTION,
        reason_code="ASSESSMENT_ISSUED" if issued else "ASSESSMENT_HANDED_IN",
        resource_kind="assignment_round",
        resource_uuid=round_uuid,
        dedupe_key=dedupe_key,
        after_state={
            "source_snapshot_digest": digest,
            "evaluator_version": NATIVE_GRADING_CONTRACT_VERSION,
            **release_binding,
        },
    )
    return True


async def issue_native_grading_contract(
    db: AsyncSession, assignment: Assignment, actor: PublicUser
) -> bool:
    """Reserve the managed submission round before exposing gradable tasks."""
    if assignment.ungraded:
        return False
    submission = await lock_or_reserve_native_round(db, assignment, actor.id)
    if submission.submission_status not in (
        AssignmentUserSubmissionStatus.PENDING,
        AssignmentUserSubmissionStatus.NOT_SUBMITTED,
    ):
        return False
    return await record_grading_contract(db, assignment, submission, actor, issued=True)


async def lock_or_reserve_native_round(
    db: AsyncSession, assignment: Assignment, user_id: int
) -> AssignmentUserSubmission:
    """Lock the same row as hand-in, reserving it if this is the first write."""
    await lock_managed_assessment_source(db, assignment)
    query = (
        select(AssignmentUserSubmission)
        .where(
            AssignmentUserSubmission.assignment_id == assignment.id,
            AssignmentUserSubmission.user_id == user_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    submission = (await db.execute(query)).scalar_one_or_none()
    if submission is None:
        now = datetime.now(UTC).isoformat()
        submission = AssignmentUserSubmission(
            assignment_id=assignment.id,
            user_id=user_id,
            assignmentusersubmission_uuid=f"assignmentusersubmission_{uuid4()}",
            submission_status=AssignmentUserSubmissionStatus.PENDING,
            grade=0,
            creation_date=now,
            update_date=now,
        )
        try:
            async with db.begin_nested():
                db.add(submission)
                await db.flush()
        except IntegrityError:
            submission = (await db.execute(query)).scalar_one_or_none()
            if submission is None:
                raise
    return submission


async def lock_managed_assessment_source(
    db: AsyncSession, assignment: Assignment
) -> None:
    """Serialize against removal of the assignment or one of its parents."""
    await lock_organization(db, assignment.org_id)
    if (
        await db.scalar(select(Assignment.id).where(Assignment.id == assignment.id))
        is None
    ):
        raise HTTPException(status_code=404, detail="Assignment not found")


async def matching_grading_contract(
    db: AsyncSession,
    assignment: Assignment,
    submission: AssignmentUserSubmission,
    tasks: Sequence[AssignmentTask],
) -> StudyBusinessEvent | None:
    round_uuid = native_round_uuid(
        assignment.org_id,
        submission.assignmentusersubmission_uuid,
        submission.attempt_number,
    )
    contract = (
        await db.execute(
            select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == assignment.org_id,
                StudyBusinessEvent.action == CONTRACT_FROZEN_ACTION,
                StudyBusinessEvent.resource_kind == "assignment_round",
                StudyBusinessEvent.resource_uuid == round_uuid,
                StudyBusinessEvent.dedupe_key == f"assessment_contract:{round_uuid}",
            )
        )
    ).scalar_one_or_none()
    if (
        contract is not None
        and isinstance(contract.after_state, dict)
        and contract.after_state.get("source_snapshot_digest") == grading_contract_digest(assignment, tasks)
        and contract.after_state.get("evaluator_version") == NATIVE_GRADING_CONTRACT_VERSION
    ):
        return contract
    return None


def task_marks_digest(tasks: list[dict]) -> str:
    return canonical_request_hash(
        sorted(
            (row["assignment_task_uuid"], row["grade"], row["manually_graded"])
            for row in tasks
        )
    )


async def _response_manifest(
    db: AsyncSession, assignment: Assignment, submission: AssignmentUserSubmission
):
    rows = (
        await db.execute(
            select(
                AssignmentTask.assignment_task_uuid,
                AssignmentTaskSubmission.task_submission,
            )
            .join(
                AssignmentTaskSubmission,
                AssignmentTaskSubmission.assignment_task_id == AssignmentTask.id,
            )
            .where(
                AssignmentTask.assignment_id == assignment.id,
                AssignmentTaskSubmission.user_id == submission.user_id,
            )
            .order_by(AssignmentTask.assignment_task_uuid)
        )
    ).all()
    round_uuid = native_round_uuid(
        assignment.org_id,
        submission.assignmentusersubmission_uuid,
        submission.attempt_number,
    )
    responses = [list(row) for row in rows]
    digest = canonical_request_hash([round_uuid, responses])
    return round_uuid, digest, responses


async def record_assessment_responses(
    db: AsyncSession,
    assignment: Assignment,
    submission: AssignmentUserSubmission,
    actor: PublicUser,
) -> None:
    round_uuid, digest, _ = await _response_manifest(db, assignment, submission)
    await append_business_event(
        db,
        org_id=assignment.org_id,
        actor_user_id=actor.id,
        actor_capacity="session_user",
        action=RESPONSES_FROZEN_ACTION,
        reason_code="ASSESSMENT_RESPONSES_HANDED_IN",
        resource_kind="assignment_round",
        resource_uuid=round_uuid,
        dedupe_key=f"assessment_responses:{round_uuid}",
        after_state={"submission_content_digest": digest},
    )


async def assessment_responses_match_hand_in(
    db: AsyncSession,
    assignment: Assignment,
    submission: AssignmentUserSubmission,
) -> bool:
    round_uuid, digest, _ = await _response_manifest(db, assignment, submission)
    state = await _hand_in_response_state(db, assignment.org_id, round_uuid)
    # Earlier submissions have no full manifest and keep their existing grading behavior.
    return state is None or state.get("submission_content_digest") == digest


async def _hand_in_response_state(
    db: AsyncSession, org_id: int, round_uuid: str
) -> dict | None:
    return await db.scalar(
        select(StudyBusinessEvent.after_state).where(
            StudyBusinessEvent.org_id == org_id,
            StudyBusinessEvent.action == RESPONSES_FROZEN_ACTION,
            StudyBusinessEvent.dedupe_key == f"assessment_responses:{round_uuid}",
        )
    )


async def archive_assessment_responses_before_retry(
    db: AsyncSession,
    assignment: Assignment,
    submission: AssignmentUserSubmission,
) -> None:
    """Archive exact response records in the transaction that clears the live rows."""
    db.add(await verified_retry_response_archive(db, assignment, submission))
    await db.flush()


async def verified_retry_response_archive(
    db: AsyncSession,
    assignment: Assignment,
    submission: AssignmentUserSubmission,
) -> StudyAssessmentResponseArchive:
    now = datetime.now(UTC)
    org = await db.get(Organization, assignment.org_id)
    (policy,) = await _current_policies(db, org, (StudyPolicyKey.D_13,), now)
    policy_body = StudyPolicyBody.model_validate(policy.body)
    retention = policy_body.specified_value("retention")
    days = (
        retention.get("managed_assessment_archive_days")
        if isinstance(retention, dict)
        else None
    )
    if type(days) is not int or days <= 0:
        raise HTTPException(
            409, "The managed assessment archive retention period is not approved."
        )
    try:
        retain_until = now + timedelta(days=days)
    except OverflowError as error:
        raise HTTPException(
            409, "The approved assessment archive period cannot be applied."
        ) from error

    round_uuid, digest, responses = await _response_manifest(db, assignment, submission)
    state = await _hand_in_response_state(db, assignment.org_id, round_uuid)
    if state is None or state.get("submission_content_digest") != digest:
        raise HTTPException(
            409, "The prior assessment responses cannot be verified for retry."
        )
    return StudyAssessmentResponseArchive(
        org_id=assignment.org_id,
        user_id=submission.user_id,
        policy_version_id=policy.id,
        round_uuid=round_uuid,
        content_digest=digest,
        responses=responses,
        retain_until=retain_until,
    )


async def latest_assessment_result(
    db: AsyncSession,
    assignment: Assignment,
    submission: AssignmentUserSubmission,
    *,
    attempt_number: int | None = None,
) -> StudyBusinessEvent | None:
    round_uuid = native_round_uuid(
        assignment.org_id,
        submission.assignmentusersubmission_uuid,
        submission.attempt_number if attempt_number is None else attempt_number,
    )
    return (
        await db.execute(
            select(StudyBusinessEvent)
            .where(
                StudyBusinessEvent.org_id == assignment.org_id,
                StudyBusinessEvent.resource_uuid == round_uuid,
                StudyBusinessEvent.action == RESULT_RECORDED_ACTION,
            )
            .order_by(StudyBusinessEvent.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def latest_assessment_results(
    db: AsyncSession,
    assignment: Assignment,
    submissions: Sequence[AssignmentUserSubmission],
) -> dict[int, StudyBusinessEvent]:
    round_to_submission = {
        native_round_uuid(
            assignment.org_id, sub.assignmentusersubmission_uuid, sub.attempt_number
        ): sub.id
        for sub in submissions
        if sub.submission_status == AssignmentUserSubmissionStatus.GRADED
    }
    if not round_to_submission:
        return {}
    latest_ids = (
        select(func.max(StudyBusinessEvent.id))
        .where(
            StudyBusinessEvent.org_id == assignment.org_id,
            StudyBusinessEvent.action == RESULT_RECORDED_ACTION,
            StudyBusinessEvent.resource_uuid.in_(round_to_submission),
        )
        .group_by(StudyBusinessEvent.resource_uuid)
    )
    results = (
        await db.execute(
            select(StudyBusinessEvent).where(StudyBusinessEvent.id.in_(latest_ids))
        )
    ).scalars()
    return {round_to_submission[row.resource_uuid]: row for row in results}


async def record_assessment_result(
    db: AsyncSession,
    assignment: Assignment,
    submission: AssignmentUserSubmission,
    contract: StudyBusinessEvent,
    computed: dict,
    reviewer: PublicUser | APITokenUser | None,
) -> None:
    """Append the aggregate while the caller holds the native attempt lock."""
    previous = await latest_assessment_result(db, assignment, submission)
    round_uuid = native_round_uuid(
        assignment.org_id,
        submission.assignmentusersubmission_uuid,
        submission.attempt_number,
    )
    result = {
        "score_points": computed["grade"],
        "maximum_points": computed["max_grade"],
        "passing_threshold": computed["passing_threshold"],
        "passed": computed["passed"],
        "source_snapshot_digest": contract.after_state["source_snapshot_digest"],
        "evaluator_version": contract.after_state["evaluator_version"],
        "result_digest": task_marks_digest(computed["tasks"]),
        "grading_type": assignment.grading_type.value,
    }
    if previous and all(
        previous.after_state.get(key) == value for key, value in result.items()
    ):
        return

    version = previous.after_state["version"] + 1 if previous else 1
    human = isinstance(reviewer, PublicUser)
    machine = (
        f"assignment_token_{reviewer.id}"
        if isinstance(reviewer, APITokenUser)
        else "native_assignment_grader"
    )
    await append_business_event(
        db,
        org_id=assignment.org_id,
        actor_user_id=reviewer.id if human else None,
        actor_machine_id=None if human else machine,
        actor_capacity="educator" if human else "automated_grader",
        action=RESULT_RECORDED_ACTION,
        reason_code="ASSESSMENT_RESULT_REVISED"
        if previous
        else "ASSESSMENT_RESULT_FINALIZED",
        resource_kind="assignment_round",
        resource_uuid=round_uuid,
        dedupe_key=f"assessment_result:{round_uuid}:{version}",
        after_state={
            **result,
            "grading_contract_event_uuid": contract.event_uuid,
            "version": version,
            "previous_event_uuid": previous.event_uuid if previous else None,
        },
    )
