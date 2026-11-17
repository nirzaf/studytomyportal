"""Verify managed grade finalization against the migrated PostgreSQL database."""

from asyncio import Barrier, Event, TaskGroup, sleep, timeout
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlmodel import select

from src.db.courses.activities import Activity, ActivitySubTypeEnum, ActivityTypeEnum
from src.db.courses.assignments import (
    Assignment,
    AssignmentTask,
    AssignmentTaskSubmission,
    AssignmentTaskSubmissionUpdate,
    AssignmentTaskTypeEnum,
    AssignmentUserSubmission,
    AssignmentUserSubmissionStatus,
    GradingTypeEnum,
)
from src.db.courses.chapters import Chapter
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.governance import StudyBusinessEvent
from src.db.trails import Trail
from src.db.users import PublicUser, User
from src.services.courses.activities import assignments
from src.services.studytomy import assignment_adapter, grading


@pytest.fixture
async def managed_round(postgres_session_factory):
    tag = uuid4().hex
    async with postgres_session_factory() as db:
        org = Organization(
            name="Synthetic grading",
            org_uuid=f"org_{tag}",
            slug=f"grading-{tag}",
            email=f"{tag}@example.invalid",
        )
        user = User(
            username=f"grader_{tag}",
            first_name="Synthetic",
            last_name="Grader",
            user_uuid=f"user_{tag}",
            email=f"{tag}@example.invalid",
        )
        db.add_all([org, user])
        await db.flush()
        course = Course(
            name="Managed grading",
            course_uuid=f"course_{tag}",
            org_id=org.id,
            public=False,
            published=False,
            open_to_contributors=False,
            studytomy_managed=True,
        )
        db.add(course)
        await db.flush()
        chapter = Chapter(name="Synthetic", course_id=course.id, org_id=org.id)
        activity = Activity(
            name="Synthetic",
            activity_uuid=f"activity_{tag}",
            course_id=course.id,
            org_id=org.id,
            activity_type=ActivityTypeEnum.TYPE_ASSIGNMENT,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_ASSIGNMENT_ANY,
        )
        db.add_all([chapter, activity])
        await db.flush()
        parents = {
            "course_id": course.id,
            "chapter_id": chapter.id,
            "activity_id": activity.id,
        }
        assignment = Assignment(
            title="Synthetic",
            description="",
            assignment_uuid=f"assignment_{tag}",
            org_id=org.id,
            grading_type=GradingTypeEnum.NUMERIC,
            **parents,
        )
        db.add(assignment)
        await db.flush()
        task = AssignmentTask(
            title="Synthetic",
            description="",
            hint="",
            assignment_task_uuid=f"task_{tag}",
            assignment_id=assignment.id,
            assignment_type=AssignmentTaskTypeEnum.NUMBER_ANSWER,
            contents={"correct_value": "25", "tolerance": "0"},
            max_grade_value=100,
            org_id=org.id,
            creation_date="",
            update_date="",
            **parents,
        )
        db.add(task)
        await db.flush()
        reviewer = PublicUser.model_validate(user)
        assert await grading.issue_native_grading_contract(db, assignment, reviewer)
        assert not await grading.issue_native_grading_contract(db, assignment, reviewer)
        attempt = (
            await db.execute(
                select(AssignmentUserSubmission).where(
                    AssignmentUserSubmission.assignment_id == assignment.id,
                    AssignmentUserSubmission.user_id == user.id,
                )
            )
        ).scalar_one()
        assert attempt.submission_status == AssignmentUserSubmissionStatus.PENDING
        db.add(
            AssignmentTaskSubmission(
                assignment_task_submission_uuid=f"task_submission_{tag}",
                task_submission={"answer": 25},
                task_submission_grade_feedback="",
                assignment_type=task.assignment_type,
                assignment_task_id=task.id,
                user_id=user.id,
                creation_date="",
                update_date="",
                **parents,
            )
        )
        assert not await grading.record_grading_contract(
            db, assignment, attempt, reviewer
        )
        await db.commit()
        return org, user, course, assignment, task, attempt, reviewer


@pytest.mark.studytomy_postgres
async def test_concurrent_finalizers_record_one_result_revision(
    postgres_session_factory, monkeypatch, managed_round
):
    org, user, course, assignment, _, attempt, reviewer = managed_round
    async with postgres_session_factory() as db:
        with pytest.raises(HTTPException) as retained:
            await assignment_adapter.ensure_native_source_deletable(
                db, org.id, "course", course.course_uuid
            )
        assert retained.value.detail["code"] == "MANAGED_ASSESSMENT_SOURCE_RETAINED"
        stored = await db.get(AssignmentUserSubmission, attempt.id)
        stored.submission_status = AssignmentUserSubmissionStatus.SUBMITTED
        await db.commit()

    monkeypatch.setattr(assignments, "record_audit_event", AsyncMock())
    monkeypatch.setattr(assignments, "notify_ee_learner_progress", lambda *_: None)
    original_record = grading.record_assessment_result
    first_inside_lock = Event()
    release_first = Event()
    second_connected = Event()
    second_pid = None

    async def hold_first(*args):
        if not first_inside_lock.is_set():
            first_inside_lock.set()
            await release_first.wait()
        await original_record(*args)

    monkeypatch.setattr(grading, "record_assessment_result", hold_first)

    async def finalize(*, second=False):
        nonlocal second_pid
        async with postgres_session_factory() as db:
            stored = await db.get(AssignmentUserSubmission, attempt.id)
            stored_assignment = await db.get(Assignment, assignment.id)
            stored_course = await db.get(Course, course.id)
            if second:
                second_pid = await db.scalar(text("SELECT pg_backend_pid()"))
                second_connected.set()
            return await assignments._apply_grade_and_finalize(
                stored_assignment,
                stored_course,
                user.id,
                stored,
                db,
                dispatch_webhook=False,
                reviewer=reviewer,
            )

    async with timeout(20), TaskGroup() as group:
        first = group.create_task(finalize())
        await first_inside_lock.wait()
        second = group.create_task(finalize(second=True))
        await second_connected.wait()
        async with postgres_session_factory() as observer:
            while not await observer.scalar(
                text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                {"pid": second_pid},
            ):
                await sleep(0.01)
        release_first.set()

    assert first.result()["grade"] == second.result()["grade"] == 100
    async with postgres_session_factory() as db:
        result = (
            await db.execute(
                select(StudyBusinessEvent).where(
                    StudyBusinessEvent.org_id == org.id,
                    StudyBusinessEvent.action == grading.RESULT_RECORDED_ACTION,
                )
            )
        ).scalar_one()
        stored = await db.get(AssignmentUserSubmission, attempt.id)
        assert result.after_state["version"] == 1
        assert result.after_state["score_points"] == stored.grade == 100
        assert result.actor_user_id == reviewer.id
        assert stored.submission_status == AssignmentUserSubmissionStatus.GRADED
        listed = await grading.latest_assessment_results(db, assignment, [stored])
        assert listed[stored.id].event_uuid == result.event_uuid


@pytest.mark.studytomy_postgres
async def test_late_answer_save_serializes_with_attempt_transition(
    postgres_session_factory, monkeypatch, mock_request, managed_round
):
    org, user, _, assignment, task, attempt, learner = managed_round
    async with postgres_session_factory() as db:
        trail = Trail(
            org_id=org.id,
            user_id=user.id,
            trail_uuid=f"trail_{uuid4()}",
            creation_date="",
            update_date="",
        )
        db.add(trail)
        await db.commit()
    monkeypatch.setattr(assignments, "check_resource_access", AsyncMock())
    monkeypatch.setattr(assignments, "verify_activity_reader_access", AsyncMock())
    monkeypatch.setattr(
        assignments,
        "authorization_verify_based_on_roles",
        AsyncMock(
            side_effect=lambda _req, _uid, action, _course, _db: action == "read"
        ),
    )
    monkeypatch.setattr(
        assignments, "check_trail_presence", AsyncMock(return_value=trail)
    )
    monkeypatch.setattr(assignments, "record_audit_event", AsyncMock())
    monkeypatch.setattr(assignments, "track", AsyncMock())
    monkeypatch.setattr(assignments, "dispatch_webhooks", AsyncMock())
    monkeypatch.setattr(
        assignments, "is_course_fully_completed", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        assignments, "check_course_completion_and_create_certificate", AsyncMock()
    )
    monkeypatch.setattr(assignments, "notify_ee_learner_progress", lambda *_: None)
    hand_in_has_lock = Event()
    release_hand_in = Event()
    save_connected = Event()
    save_pid = None
    original_freeze = assignment_adapter.freeze_native_assignment_answers

    async def pause_after_lock(*args, **kwargs):
        hand_in_has_lock.set()
        await release_hand_in.wait()
        return await original_freeze(*args, **kwargs)

    monkeypatch.setattr(
        assignment_adapter, "freeze_native_assignment_answers", pause_after_lock
    )

    async def hand_in():
        async with postgres_session_factory() as db:
            return await assignments.create_assignment_submission(
                mock_request, assignment.assignment_uuid, learner, db
            )

    async def save_late_answer():
        nonlocal save_pid
        async with postgres_session_factory() as db:
            save_pid = await db.scalar(text("SELECT pg_backend_pid()"))
            save_connected.set()
            with pytest.raises(HTTPException, match="already been handed in") as denied:
                await assignments.handle_assignment_task_submission(
                    mock_request,
                    task.assignment_task_uuid,
                    AssignmentTaskSubmissionUpdate(task_submission={"answer": 999}),
                    learner,
                    db,
                )
            assert denied.value.status_code == 403

    async with timeout(20), TaskGroup() as group:
        submitted = group.create_task(hand_in())
        await hand_in_has_lock.wait()
        group.create_task(save_late_answer())
        await save_connected.wait()
        async with postgres_session_factory() as observer:
            while not await observer.scalar(
                text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                {"pid": save_pid},
            ):
                await sleep(0.01)
        release_hand_in.set()

    assert (
        submitted.result().submission_status == AssignmentUserSubmissionStatus.SUBMITTED
    )
    async with postgres_session_factory() as db:
        stored = await db.get(AssignmentUserSubmission, attempt.id)
        answer = (
            await db.execute(
                select(AssignmentTaskSubmission).where(
                    AssignmentTaskSubmission.assignment_task_id == task.id,
                    AssignmentTaskSubmission.user_id == user.id,
                )
            )
        ).scalar_one()
        assert stored.submission_status == AssignmentUserSubmissionStatus.SUBMITTED
        assert answer.task_submission == {"answer": 25}


@pytest.mark.studytomy_postgres
@pytest.mark.parametrize(
    ("correct_score", "historical"), [(False, False), (True, False), (True, True)]
)
async def test_review_evidence_requires_direct_independent_authorized_reviewer(
    postgres_session_factory,
    managed_round,
    monkeypatch,
    mock_request,
    correct_score,
    historical,
):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from src.core.events.database import get_db_session
    from src.db.roles import Role, RoleTypeEnum
    from src.db.studytomy.family import StudyLearnerProfile
    from src.db.studytomy.governance import (
        StudyOwnerFunction,
        StudyPolicyApproval,
        StudyPolicyKey,
    )
    from src.db.user_organizations import UserOrganization
    from src.routers.studytomy.adaptive import router as adaptive_router
    from src.security.studytomy_access import StudytomyCapabilityName
    from src.services.auth.session import mint_session_tokens
    from src.services.studytomy.business_events import append_business_event
    from src.services.studytomy.policies import POLICY_REQUIRED_RESPONSIBILITIES
    from src.tests.studytomy.test_studytomy_access import (
        _seed_active_owner,
        _seed_approved_policy,
        _seed_staff_scope,
    )

    org, learner, course, assignment, task, attempt, original_marker = managed_round
    monkeypatch.setattr(assignments, "record_audit_event", AsyncMock())
    monkeypatch.setattr(assignments, "notify_ee_learner_progress", lambda *_: None)
    monkeypatch.setattr(assignments, "check_resource_access", AsyncMock())
    monkeypatch.setattr(assignments, "verify_activity_reader_access", AsyncMock())
    monkeypatch.setattr(
        assignments,
        "authorization_verify_based_on_roles",
        AsyncMock(
            side_effect=lambda _req, _uid, action, _course, _db: action == "read"
        ),
    )
    monkeypatch.setattr(assignments, "track", AsyncMock())
    monkeypatch.setattr(assignments, "dispatch_webhooks", AsyncMock())
    monkeypatch.setattr(
        assignments, "is_course_fully_completed", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        assignments, "check_course_completion_and_create_certificate", AsyncMock()
    )
    tag = uuid4().hex
    async with postgres_session_factory() as db:
        reviewer = User(
            username=f"reviewer_{tag}",
            email=f"{tag}@example.invalid",
            user_uuid=f"user_{tag}",
            first_name="Synthetic",
            last_name="Reviewer",
        )
        role = Role(
            name="Synthetic moderator",
            org_id=org.id,
            role_uuid=f"role_{tag}",
            role_type=RoleTypeEnum.TYPE_ORGANIZATION,
            rights={"studytomy": {"assessment_moderation": True}},
        )
        profile = StudyLearnerProfile(
            org_id=org.id,
            user_id=learner.id,
            created_by_user_id=learner.id,
        )
        db.add_all([reviewer, role, profile])
        await db.flush()
        db.add(
            UserOrganization(
                user_id=reviewer.id,
                org_id=org.id,
                role_id=role.id,
                creation_date="",
                update_date="",
            )
        )
        await db.commit()
        db.add(
            UserOrganization(
                user_id=learner.id,
                org_id=org.id,
                role_id=role.id,
                creation_date="",
                update_date="",
            )
        )
        await db.commit()
        owner = await _seed_active_owner(
            db, org, reviewer.id, reviewer.id, StudyOwnerFunction.EDUCATION
        )
        capability = StudytomyCapabilityName.ASSESSMENT_MODERATION
        await _seed_staff_scope(
            db, org.id, owner.id, capability, "course", course.course_uuid, reviewer.id
        )

        app = FastAPI()
        app.include_router(adaptive_router)
        app.dependency_overrides[get_db_session] = lambda: db
        path = (
            f"/orgs/{org.org_uuid}/courses/{course.course_uuid}/assignments/"
            f"{assignment.assignment_uuid}/submissions/{attempt.assignmentusersubmission_uuid}/review-evidence"
        )

        def headers(method, actor=reviewer):
            token = mint_session_tokens(
                actor.email, amr=method, org_id=org.id
            ).access_token
            return {"Authorization": f"Bearer {token}"}

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            direct = headers("password")
            assert (
                await client.get(path, headers=headers("api_token"))
            ).status_code == 403
            assert (await client.get(path, headers=direct)).status_code == 404
            await _seed_staff_scope(
                db,
                org.id,
                owner.id,
                capability,
                "learner",
                profile.learner_uuid,
                reviewer.id,
            )
            missing = await client.get(path, headers=direct)
            assert missing.status_code == 409
            assert missing.json()["detail"]["code"] == "POLICY_NOT_APPROVED"
            assert "no-store" in missing.headers["cache-control"]

            owners = {StudyOwnerFunction.EDUCATION: owner}
            for key in (StudyPolicyKey.D_04, StudyPolicyKey.D_08, StudyPolicyKey.D_15):
                policy = await _seed_approved_policy(
                    db,
                    org,
                    reviewer.id,
                    key,
                    specified={
                        "attempts": {"managed_retry_grants_enabled": True},
                        "assistance": {"managed_integrity_review_enabled": True},
                    }
                    if historical and key == StudyPolicyKey.D_08
                    else None,
                )
                for responsibility in POLICY_REQUIRED_RESPONSIBILITIES[key]:
                    if responsibility not in owners:
                        owners[responsibility] = await _seed_active_owner(
                            db, org, reviewer.id, reviewer.id, responsibility
                        )
                    db.add(
                        StudyPolicyApproval(
                            org_id=org.id,
                            policy_version_id=policy.id,
                            responsibility=responsibility.value,
                            approver_user_id=reviewer.id,
                            owner_assignment_id=owners[responsibility].id,
                            evidence_reference="synthetic-review-policy",
                            rationale="Synthetic policy fixture",
                            approved_at=datetime.now(UTC),
                        )
                    )
                await db.commit()
            assert (await client.get(path, headers=direct)).status_code == 404

            assignment = await db.get(Assignment, assignment.id)
            assignment.due_date = "2000-01-01"
            await db.commit()
            assert await assignments._learner_assignment_past_due(
                db, assignment, course, learner.id
            )
            async with postgres_session_factory() as denied_save:
                with pytest.raises(
                    HTTPException, match="deadline has passed"
                ) as blocked:
                    await assignments.handle_assignment_task_submission(
                        mock_request,
                        task.assignment_task_uuid,
                        AssignmentTaskSubmissionUpdate(task_submission={"answer": 25}),
                        original_marker,
                        denied_save,
                    )
                assert blocked.value.status_code == 403
            deadline_path = (
                path.removesuffix("/review-evidence") + "/deadline-adjustments"
            )
            extension = {
                "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                "reason": "SERVICE_INTERRUPTION",
            }
            adjusted = await client.post(deadline_path, headers=direct, json=extension)
            assert adjusted.status_code == 200, adjusted.text
            assert (
                await client.post(deadline_path, headers=direct, json=extension)
            ).json() == adjusted.json()
            assert not await assignments._learner_assignment_past_due(
                db, assignment, course, learner.id
            )
            assert await assignments._learner_assignment_past_due(
                db, assignment, course, reviewer.id
            )
            assert assignment.due_date == "2000-01-01"

            visible = await assignments.read_assignment(
                mock_request,
                assignment.assignment_uuid,
                original_marker,
                db,
            )
            assert datetime.fromisoformat(visible.due_date) == datetime.fromisoformat(
                extension["expires_at"]
            )
            saved = await assignments.handle_assignment_task_submission(
                mock_request,
                task.assignment_task_uuid,
                AssignmentTaskSubmissionUpdate(task_submission={"answer": 25}),
                original_marker,
                db,
            )
            assert saved.task_submission == {"answer": 25}
            trail = Trail(
                org_id=org.id,
                user_id=learner.id,
                trail_uuid=f"trail_{uuid4()}",
                creation_date="",
                update_date="",
            )
            db.add(trail)
            await db.commit()
            monkeypatch.setattr(
                assignments, "check_trail_presence", AsyncMock(return_value=trail)
            )
            handed_in = await assignments.create_assignment_submission(
                mock_request,
                assignment.assignment_uuid,
                original_marker,
                db,
            )
            assert (
                handed_in.submission_status == AssignmentUserSubmissionStatus.SUBMITTED
            )
            stored = await db.get(AssignmentUserSubmission, attempt.id)
            manifest = await db.scalar(
                select(StudyBusinessEvent).where(
                    StudyBusinessEvent.org_id == org.id,
                    StudyBusinessEvent.action == grading.RESPONSES_FROZEN_ACTION,
                )
            )
            assert set(manifest.after_state) == {"submission_content_digest"}
            assert len(manifest.after_state["submission_content_digest"]) == 64
            submitted_answer = await db.get(AssignmentTaskSubmission, saved.id)
            submitted_answer.task_submission = {"answer": 999}
            await db.commit()
            refused = await assignments._apply_grade_and_finalize(
                assignment,
                course,
                learner.id,
                stored,
                db,
                dispatch_webhook=False,
                reviewer=original_marker,
            )
            assert refused["reason"] == "managed_assessment_responses_unverified"
            assert stored.submission_status == AssignmentUserSubmissionStatus.SUBMITTED
            assert stored.grade == 0
            submitted_answer.task_submission = {"answer": 25}
            await db.commit()
            await assignments._apply_grade_and_finalize(
                assignment,
                course,
                learner.id,
                stored,
                db,
                dispatch_webhook=False,
                reviewer=original_marker,
            )
            allowed = await client.get(path, headers=direct)
            assert allowed.status_code == 200, allowed.text
            assert "no-store" in allowed.headers["cache-control"]
            result = allowed.json()
            if historical:
                integrity_path = (
                    path.removesuffix("/review-evidence") + "/integrity-cases"
                )
                eligibility_path = integrity_path.removesuffix("integrity-cases") + "integrity-intake"
                attempt_query = {"attempt_number": stored.attempt_number}
                learner_headers = headers("password", learner)
                denied_availability = await client.get(
                    eligibility_path, headers=learner_headers, params=attempt_query,
                )
                assert denied_availability.status_code == 404
                available = await client.get(
                    eligibility_path, headers=direct, params=attempt_query,
                )
                assert available.status_code == 200, available.text
                assert available.json() == {"can_record": True}
                assert "no-store" in available.headers["cache-control"]
                stale = await client.post(
                    integrity_path, headers=direct, json={"source": "automated_signal"},
                    params={"attempt_number": stored.attempt_number + 1},
                )
                assert stale.status_code == 409
                from src.services.studytomy import assessment_integrity

                # Release the preflight transaction held by the fixture's shared session.
                await db.commit()
                barrier = Barrier(2)
                intake_sessions = set()
                lock_organization = assessment_integrity.lock_organization

                async def intake_db_session():
                    async with postgres_session_factory() as session:
                        yield session

                async def synchronized_lock(session, org_id):
                    intake_sessions.add(session)
                    await barrier.wait()
                    await lock_organization(session, org_id)

                def send_intake():
                    return client.post(
                        integrity_path,
                        headers=direct,
                        json={"source": "automated_signal"},
                        params=attempt_query,
                    )

                with monkeypatch.context() as concurrent_intake:
                    concurrent_intake.setitem(
                        app.dependency_overrides, get_db_session, intake_db_session
                    )
                    concurrent_intake.setattr(
                        assessment_integrity, "lock_organization", synchronized_lock
                    )
                    async with timeout(15), TaskGroup() as concurrent:
                        first = concurrent.create_task(send_intake())
                        second = concurrent.create_task(send_intake())
                assert len(intake_sessions) == 2
                intake = first.result()
                assert intake.status_code == second.result().status_code == 200, (
                    intake.text, second.result().text
                )
                assert intake.json() == second.result().json()
                events = (
                    await db.exec(
                        select(StudyBusinessEvent.event_uuid).where(
                            StudyBusinessEvent.org_id == org.id,
                            StudyBusinessEvent.action == assessment_integrity.CONCERN_RECORDED,
                            StudyBusinessEvent.resource_uuid == result["round_uuid"],
                        )
                    )
                ).all()
                assert events == [intake.json()["reference"]]
                assert (
                    await client.get(eligibility_path, headers=direct, params=attempt_query)
                ).json() == {"can_record": False}
                assert intake.status_code == 200, intake.text
                assert intake.json()["status"] == "awaiting_review"
                case_context = {
                    "assessment_title": assignment.title,
                    "attempt_number": stored.attempt_number,
                }
                owned_cases = f"/orgs/{org.org_uuid}/me/assessment-integrity-cases"
                assert (
                    await client.get(owned_cases, headers=learner_headers)
                ).json() == [{**intake.json(), **case_context}]
                assert (await client.get(owned_cases, headers=direct)).json() == []
                response_path = f"{owned_cases}/{intake.json()['reference']}/response"
                reply = {"response": "dispute_concern"}
                assert (
                    await client.put(response_path, headers=direct, json=reply)
                ).status_code == 404
                responded = await client.put(
                    response_path, headers=learner_headers, json=reply
                )
                assert responded.status_code == 200, responded.text
                assert responded.json()["status"] == "response_received"
                assert "no-store" in responded.headers["cache-control"]
                round_has_open_integrity_concern = assessment_integrity.round_has_open_integrity_concern

                assert await round_has_open_integrity_concern(
                    db, org.id, result["round_uuid"]
                )
                staff_review_path = f"{integrity_path}/{intake.json()['reference']}"
                dismissal_path = f"{staff_review_path}/dismissal"
                dismissal_body = {"reason": "insufficient_evidence"}
                assert (
                    await client.put(
                        dismissal_path, headers=direct, json=dismissal_body
                    )
                ).status_code == 404
                independent = User(
                    username=f"independent_{tag}",
                    email=f"independent_{tag}@example.invalid",
                    user_uuid=f"user_independent_{tag}",
                    first_name="Synthetic",
                    last_name="Moderator",
                )
                db.add(independent)
                await db.flush()
                db.add(
                    UserOrganization(
                        org_id=org.id,
                        user_id=independent.id,
                        role_id=role.id,
                        creation_date="",
                        update_date="",
                    )
                )
                await db.commit()
                independent_owner = await _seed_active_owner(
                    db,
                    org,
                    independent.id,
                    reviewer.id,
                    StudyOwnerFunction.EDUCATION,
                )
                await _seed_staff_scope(
                    db,
                    org.id,
                    independent_owner.id,
                    capability,
                    "course",
                    course.course_uuid,
                    reviewer.id,
                )
                await _seed_staff_scope(
                    db,
                    org.id,
                    independent_owner.id,
                    capability,
                    "learner",
                    profile.learner_uuid,
                    reviewer.id,
                )
                independent_headers = headers("password", independent)
                for denied_headers in (direct, learner_headers):
                    assert (
                        await client.get(staff_review_path, headers=denied_headers)
                    ).status_code == 404
                    assert (
                        await client.get(integrity_path, headers=denied_headers)
                    ).status_code == 404
                review = await client.get(staff_review_path, headers=independent_headers)
                assert review.status_code == 200, review.text
                assert "no-store" in review.headers["cache-control"]
                assert review.json() == {
                    **responded.json(), "response": "dispute_concern",
                }
                discovered = await client.get(integrity_path, headers=independent_headers)
                assert discovered.status_code == 200, discovered.text
                assert discovered.json() == review.json()
                assert (
                    await client.get(f"{integrity_path}/{uuid4()}", headers=independent_headers)
                ).status_code == 404
                dismissed = await client.put(
                    dismissal_path,
                    headers=independent_headers,
                    json=dismissal_body,
                )
                assert dismissed.status_code == 200, dismissed.text
                assert dismissed.json()["status"] == "dismissed"
                assert (
                    await client.get(owned_cases, headers=learner_headers)
                ).json() == [{**dismissed.json(), **case_context}]
                assert not await round_has_open_integrity_concern(
                    db, org.id, result["round_uuid"]
                )
                await db.refresh(stored)
                assert stored.grade == 100
            assert set(result) == {
                "round_uuid",
                "result_uuid",
                "version",
                "score_points",
                "maximum_points",
                "passed",
                "evaluator_version",
            }
            assert (
                result["score_points"],
                result["maximum_points"],
                result["version"],
            ) == (100, 100, 1)
            appeal_path = path.removesuffix("/review-evidence") + "/appeals"
            payload = {"result_uuid": result["result_uuid"]}
            assert (
                await client.post(appeal_path, headers=direct, json=payload)
            ).status_code == 404
            learner_headers = headers("password", learner)
            available = await client.get(appeal_path, headers=learner_headers)
            assert available.status_code == 200
            assert available.json() == {"receipt": None, "can_request_review": True}
            assert (await client.get(appeal_path, headers=direct)).status_code == 404
            assert (
                await client.post(
                    appeal_path,
                    headers=learner_headers,
                    json={"result_uuid": str(uuid4())},
                )
            ).status_code == 409
            opened = await client.post(
                appeal_path, headers=learner_headers, json=payload
            )
            assert opened.status_code == 200, opened.text
            assert "no-store" in opened.headers["cache-control"]
            assert opened.json()["status"] == "received"
            assert opened.json()["appealed_result_uuid"] == result["result_uuid"]
            replay = await client.post(
                appeal_path, headers=learner_headers, json=payload
            )
            assert replay.json() == opened.json()
            appeal_uuid = opened.json()["appeal_uuid"]
            status_path = f"/orgs/{org.org_uuid}/me/assessment-appeals/{appeal_uuid}"
            confirm_path = f"{appeal_path}/{appeal_uuid}/confirm"
            if historical:
                business = owners.get(
                    StudyOwnerFunction.BUSINESS
                ) or await _seed_active_owner(
                    db,
                    org,
                    reviewer.id,
                    reviewer.id,
                    StudyOwnerFunction.BUSINESS,
                )
                policy = await _seed_approved_policy(
                    db,
                    org,
                    reviewer.id,
                    StudyPolicyKey.D_13,
                    specified={"retention": {"managed_assessment_archive_days": 30}},
                )
                db.add(
                    StudyPolicyApproval(
                        org_id=org.id,
                        policy_version_id=policy.id,
                        responsibility="business",
                        approver_user_id=reviewer.id,
                        owner_assignment_id=business.id,
                        evidence_reference="synthetic-archive-policy",
                        rationale="Synthetic policy fixture",
                        approved_at=datetime.now(UTC),
                    )
                )
                assignment.allow_retries = False
                assignment.max_retries = 1
                await db.commit()
                async with postgres_session_factory() as denied_retry:
                    with pytest.raises(
                        HTTPException, match="Retries are not enabled"
                    ) as blocked:
                        await assignments.retry_assignment_submission(
                            mock_request,
                            assignment.assignment_uuid,
                            original_marker,
                            denied_retry,
                        )
                    assert blocked.value.status_code == 403
                grant_path = path.removesuffix("/review-evidence") + "/retry-grants"
                grant_body = {
                    "result_uuid": result["result_uuid"],
                    "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
                }
                stale_grant = await client.post(
                    grant_path,
                    headers=direct,
                    json={**grant_body, "result_uuid": str(uuid4())},
                )
                assert stale_grant.status_code == 409
                granted = await client.post(grant_path, headers=direct, json=grant_body)
                assert granted.status_code == 200, granted.text
                assert (
                    await client.post(grant_path, headers=direct, json=grant_body)
                ).json() == granted.json()
                learner_grade = await assignments.get_grade_assignment_submission(
                    mock_request,
                    learner.id,
                    assignment.assignment_uuid,
                    original_marker,
                    db,
                )
                assert datetime.fromisoformat(
                    learner_grade["reviewed_retry_expires_at"]
                ) == datetime.fromisoformat(grant_body["expires_at"])
                with monkeypatch.context() as instructor_access:
                    instructor_access.setattr(
                        assignments,
                        "_is_assignment_instructor",
                        AsyncMock(return_value=True),
                    )
                    staff_grade = await assignments.get_grade_assignment_submission(
                        mock_request,
                        learner.id,
                        assignment.assignment_uuid,
                        PublicUser.model_validate(reviewer),
                        db,
                    )
                assert "reviewed_retry_expires_at" not in staff_grade
                monkeypatch.setattr(assignments, "revoke_user_certificate", AsyncMock())
                monkeypatch.setattr(assignments, "sync_trailrun_status", AsyncMock())
                retried = await assignments.retry_assignment_submission(
                    mock_request,
                    assignment.assignment_uuid,
                    original_marker,
                    db,
                )
                assert retried["attempt_number"] == 2
                assert not await assignments._learner_assignment_past_due(
                    db, assignment, course, learner.id
                )
                correct_path = f"{appeal_path}/{appeal_uuid}/correct"
                assert (
                    await client.post(
                        correct_path, headers=direct, json={"score_points": 40}
                    )
                ).status_code == 404
                historical_result = await client.get(
                    path, headers=direct, params={"attempt_number": 1}
                )
                assert historical_result.json() == result
                corrected = await client.post(
                    correct_path,
                    headers=direct,
                    params={"attempt_number": 1},
                    json={"score_points": 40},
                )
                assert corrected.status_code == 200, corrected.text
                assert corrected.json()["status"] == "result_corrected"
                assert (
                    await client.get(status_path, headers=learner_headers)
                ).json() == corrected.json()
                await db.refresh(stored)
                assert (
                    stored.attempt_number,
                    stored.grade,
                    stored.submission_status,
                ) == (
                    2,
                    0,
                    AssignmentUserSubmissionStatus.PENDING,
                )
                previous = await grading.latest_assessment_result(
                    db, assignment, stored, attempt_number=1
                )
                assert previous.after_state["score_points"] == 40
                assert (
                    previous.after_state["previous_event_uuid"] == result["result_uuid"]
                )
                return
            assert (
                await client.get(status_path, headers=learner_headers)
            ).json() == opened.json()
            assert (await client.get(status_path, headers=direct)).status_code == 404
            assert (
                await client.post(confirm_path, headers=learner_headers)
            ).status_code == 404
            if correct_score:
                correct_path = f"{appeal_path}/{appeal_uuid}/correct"
                invalid = await client.post(
                    correct_path, headers=direct, json={"score_points": 101}
                )
                assert invalid.status_code == 422
                confirmed = await client.post(
                    correct_path, headers=direct, json={"score_points": 40}
                )
                assert confirmed.status_code == 200, confirmed.text
                assert confirmed.json()["status"] == "result_corrected"
                assert confirmed.json()["result_uuid"] != result["result_uuid"]
                revision = await grading.latest_assessment_result(
                    db, assignment, stored
                )
                assert (
                    revision.after_state["previous_event_uuid"] == result["result_uuid"]
                )
                assert revision.after_state["passed"] is False
                assert revision.after_state["version"] == 2
                assert (
                    await assignments._apply_grade_and_finalize(
                        assignment,
                        course,
                        learner.id,
                        stored,
                        db,
                        dispatch_webhook=False,
                    )
                )["reason"] == "managed_assessment_moderated"
                saved_task = await db.scalar(
                    select(AssignmentTaskSubmission).where(
                        AssignmentTaskSubmission.assignment_task_id == task.id,
                        AssignmentTaskSubmission.user_id == learner.id,
                    )
                )
                with monkeypatch.context() as staff_access:
                    staff_access.setattr(
                        assignments,
                        "authorization_verify_based_on_roles",
                        AsyncMock(return_value=True),
                    )
                    with pytest.raises(
                        HTTPException, match="corrected on appeal"
                    ) as blocked:
                        await assignments.handle_assignment_task_submission(
                            mock_request,
                            task.assignment_task_uuid,
                            AssignmentTaskSubmissionUpdate(
                                assignment_task_submission_uuid=saved_task.assignment_task_submission_uuid,
                                grade=80,
                                manually_graded=True,
                            ),
                            PublicUser.model_validate(reviewer),
                            db,
                        )
                    assert blocked.value.status_code == 409
                await db.refresh(saved_task)
                assert saved_task.grade == 100
                assert saved_task.manually_graded is False
            else:
                confirmed = await client.post(confirm_path, headers=direct)
                assert confirmed.status_code == 200, confirmed.text
                assert confirmed.json()["status"] == "result_confirmed"
                assert (
                    await client.post(confirm_path, headers=direct)
                ).json() == confirmed.json()
                reopen_path = f"{appeal_path}/{appeal_uuid}/reopen"
                reopen_body = {"decision_uuid": confirmed.json()["decision_uuid"]}
                stale = await client.post(
                    reopen_path, headers=direct, json={"decision_uuid": str(uuid4())}
                )
                assert stale.status_code == 409
                reopened = await client.post(
                    reopen_path, headers=direct, json=reopen_body
                )
                assert reopened.status_code == 200, reopened.text
                assert reopened.json()["status"] == "reopened"
                assert (
                    await client.post(reopen_path, headers=direct, json=reopen_body)
                ).json() == reopened.json()
                assert (
                    await client.post(confirm_path, headers=direct)
                ).status_code == 409
                confirmed_again = await client.post(
                    confirm_path,
                    headers=direct,
                    params={"expected_decision_uuid": reopened.json()["decision_uuid"]},
                )
                assert confirmed_again.status_code == 200, confirmed_again.text
                assert confirmed_again.json()["status"] == "result_confirmed"
                assert (
                    confirmed_again.json()["decision_uuid"]
                    != confirmed.json()["decision_uuid"]
                )
                assert (
                    await client.post(reopen_path, headers=direct, json=reopen_body)
                ).status_code == 409
                confirmed = confirmed_again
            status = await client.get(status_path, headers=learner_headers)
            assert status.json() == confirmed.json()
            latest = await client.get(appeal_path, headers=learner_headers)
            assert latest.json() == {
                "receipt": confirmed.json(),
                "can_request_review": correct_score,
            }
            assert (
                latest.json()["receipt"]["appealed_result_uuid"]
                == result["result_uuid"]
            )
            assert "no-store" in latest.headers["cache-control"]
            assert "no-store" in status.headers["cache-control"]
            await db.refresh(stored)
            assert stored.grade == (40 if correct_score else 100)

            await append_business_event(
                db,
                org_id=org.id,
                actor_user_id=reviewer.id,
                action="studytomy.assessment.task_marked",
                reason_code="EDUCATOR_MARK_RECORDED",
                operation_uuid=result["round_uuid"],
                resource_uuid=task.assignment_task_uuid,
                after_state={"score_points": 100, "maximum_points": 100},
            )
            await db.commit()
            denied = await client.get(path, headers=direct)
            assert denied.status_code == 404
            assert "no-store" in denied.headers["cache-control"]
            assert (await client.post(confirm_path, headers=direct)).status_code == 404
            owner.status = "ended"
            owner.effective_to = datetime.now(UTC)
            db.add(owner)
            await db.commit()
            after_approval_lapsed = await client.get(
                appeal_path, headers=learner_headers
            )
            assert after_approval_lapsed.status_code == 200
            assert after_approval_lapsed.json() == {
                "receipt": confirmed.json(),
                "can_request_review": False,
            }

            if correct_score:
                from sqlalchemy.exc import DBAPIError

                from src.db.studytomy.assessment_archive import (
                    StudyAssessmentResponseArchive,
                )

                assignment.allow_retries = True
                assignment.due_date = "2099-01-01"
                await db.commit()
                async with postgres_session_factory() as denied_retry:
                    with pytest.raises(HTTPException) as lacking_policy:
                        await assignments.retry_assignment_submission(
                            mock_request,
                            assignment.assignment_uuid,
                            original_marker,
                            denied_retry,
                        )
                    assert lacking_policy.value.detail["code"] == "POLICY_NOT_APPROVED"
                business = owners.get(StudyOwnerFunction.BUSINESS)
                if business is None:
                    business = await _seed_active_owner(
                        db,
                        org,
                        reviewer.id,
                        reviewer.id,
                        StudyOwnerFunction.BUSINESS,
                    )
                policy = await _seed_approved_policy(
                    db,
                    org,
                    reviewer.id,
                    StudyPolicyKey.D_13,
                    specified={
                        "retention": {"managed_assessment_archive_days": 30},
                        "record_access": {
                            "managed_assessment_archive_review_enabled": True
                        },
                    },
                )
                db.add(
                    StudyPolicyApproval(
                        org_id=org.id,
                        policy_version_id=policy.id,
                        responsibility="business",
                        approver_user_id=reviewer.id,
                        owner_assignment_id=business.id,
                        evidence_reference="synthetic-archive-policy",
                        rationale="Synthetic policy fixture",
                        approved_at=datetime.now(UTC),
                    )
                )
                await db.commit()
                monkeypatch.setattr(assignments, "revoke_user_certificate", AsyncMock())
                monkeypatch.setattr(assignments, "sync_trailrun_status", AsyncMock())
                retried = await assignments.retry_assignment_submission(
                    mock_request,
                    assignment.assignment_uuid,
                    original_marker,
                    db,
                )
                assert retried["attempt_number"] == 2
                archive = await db.get(
                    StudyAssessmentResponseArchive, result["round_uuid"]
                )
                assert archive.responses == [
                    [task.assignment_task_uuid, {"answer": 25}]
                ]
                assert archive.policy_version_id == policy.id
                assert (
                    archive.content_digest
                    == manifest.after_state["submission_content_digest"]
                )
                live_answer = await db.scalar(
                    select(AssignmentTaskSubmission.id).where(
                        AssignmentTaskSubmission.assignment_task_id == task.id,
                        AssignmentTaskSubmission.user_id == learner.id,
                    )
                )
                assert live_answer is None
                with (
                    pytest.raises(
                        DBAPIError, match="protected by its retention period"
                    ),
                    db.no_autoflush,
                ):
                    async with db.begin_nested():
                        await db.delete(archive)
                        await db.flush()
                await db.refresh(archive)

                archive_reviewer = User(
                    username=f"archive_{tag}",
                    email=f"archive_{tag}@example.invalid",
                    user_uuid=f"user_archive_{tag}",
                    first_name="Synthetic",
                    last_name="Archive reviewer",
                )
                db.add(archive_reviewer)
                await db.flush()
                db.add(
                    UserOrganization(
                        org_id=org.id,
                        user_id=archive_reviewer.id,
                        role_id=role.id,
                        creation_date="",
                        update_date="",
                    )
                )
                await db.commit()
                archive_owner = await _seed_active_owner(
                    db,
                    org,
                    archive_reviewer.id,
                    reviewer.id,
                    StudyOwnerFunction.EDUCATION,
                )
                archive_path = (
                    path.removesuffix("/review-evidence")
                    + "/attempts/1/archived-responses"
                )
                archive_headers = headers("password", archive_reviewer)
                await _seed_staff_scope(
                    db,
                    org.id,
                    archive_owner.id,
                    capability,
                    "course",
                    course.course_uuid,
                    reviewer.id,
                )
                assert (
                    await client.get(archive_path, headers=archive_headers)
                ).status_code == 404
                await _seed_staff_scope(
                    db,
                    org.id,
                    archive_owner.id,
                    capability,
                    "learner",
                    profile.learner_uuid,
                    reviewer.id,
                )
                private = await client.get(archive_path, headers=archive_headers)
                assert private.status_code == 200, private.text
                assert "no-store" in private.headers["cache-control"]
                assert private.json() == {
                    "round_uuid": result["round_uuid"],
                    "responses": archive.responses,
                }
                assert (
                    await client.get(archive_path, headers=learner_headers)
                ).status_code == 404
                await append_business_event(
                    db,
                    org_id=org.id,
                    actor_user_id=archive_reviewer.id,
                    action="studytomy.assessment.task_marked",
                    reason_code="EDUCATOR_MARK_RECORDED",
                    operation_uuid=result["round_uuid"],
                )
                await db.commit()
                assert (
                    await client.get(archive_path, headers=archive_headers)
                ).status_code == 404


@pytest.mark.studytomy_postgres
async def test_expired_archive_requires_an_independent_removal_executor(
    postgres_session_factory,
    managed_round,
    monkeypatch,
):
    from src.db.studytomy.assessment_archive import StudyAssessmentResponseArchive
    from src.db.studytomy.governance import StudyPolicyKey
    from src.services.studytomy import assessment_archive_retention as retention
    from src.services.studytomy.business_events import append_business_event
    from src.tests.studytomy.test_studytomy_access import _seed_approved_policy

    org, learner, _, _, _, submission, _ = managed_round
    async with postgres_session_factory() as db:
        reviewers = []
        for _ in range(2):
            tag = uuid4().hex
            user = User(
                username=f"privacy_{tag}",
                email=f"{tag}@example.invalid",
                user_uuid=f"user_{tag}",
                first_name="Synthetic",
                last_name="Privacy reviewer",
            )
            db.add(user)
            reviewers.append(user)
        await db.commit()
        approver, executor = [PublicUser.model_validate(user) for user in reviewers]
        policy = await _seed_approved_policy(
            db,
            org,
            approver.id,
            StudyPolicyKey.D_13,
            specified={"removal": {"managed_assessment_archive_removal_enabled": True}},
        )
        auth = AsyncMock()
        monkeypatch.setattr(retention, "authorize_studytomy_staff_action", auth)
        monkeypatch.setattr(
            retention, "_current_policies", AsyncMock(return_value=[policy])
        )
        round_uuid = assignment_adapter.native_round_uuid(
            org.id, submission.assignmentusersubmission_uuid, 1
        )
        db.add(
            StudyAssessmentResponseArchive(
                round_uuid=round_uuid,
                org_id=org.id,
                user_id=learner.id,
                policy_version_id=policy.id,
                content_digest="0" * 64,
                responses=[["fixture", {"answer": 25}]],
                retain_until=datetime.now(UTC) - timedelta(days=1),
            )
        )
        await db.commit()
        pending = await append_business_event(
            db,
            org_id=org.id,
            actor_user_id=learner.id,
            action="studytomy.assessment.appeal_requested",
            reason_code="LEARNER_REQUESTED_REVIEW",
            operation_uuid=round_uuid,
            resource_uuid=str(uuid4()),
        )
        await db.commit()
        with pytest.raises(HTTPException, match="open appeal") as held:
            await retention.remove_assessment_archive(db, org, approver, round_uuid)
        assert held.value.status_code == 409
        await append_business_event(
            db,
            org_id=org.id,
            actor_user_id=approver.id,
            action="studytomy.assessment.appeal_confirmed",
            reason_code="RESULT_CONFIRMED_AFTER_REVIEW",
            resource_kind="assessment_appeal",
            resource_uuid=pending.event_uuid,
            operation_uuid=round_uuid,
            after_state={
                "status": "result_confirmed",
                "result_uuid": pending.resource_uuid,
            },
        )
        await db.commit()
        approved = await retention.remove_assessment_archive(
            db, org, approver, round_uuid
        )
        assert (
            await retention.remove_assessment_archive(db, org, approver, round_uuid)
            == approved
        )
        assert await db.get(StudyAssessmentResponseArchive, round_uuid) is not None
        with pytest.raises(
            HTTPException, match="different assigned reviewer"
        ) as denied:
            await retention.remove_assessment_archive(
                db, org, approver, round_uuid, approved.reference
            )
        assert denied.value.status_code == 403
        removed = await retention.remove_assessment_archive(
            db, org, executor, round_uuid, approved.reference
        )
        assert await db.get(StudyAssessmentResponseArchive, round_uuid) is None
        assert (
            await retention.remove_assessment_archive(
                db, org, executor, round_uuid, approved.reference
            )
            == removed
        )
        assert auth.await_args.kwargs["target_resource_uuid"] == round_uuid
        audit = await db.scalar(
            select(StudyBusinessEvent).where(
                StudyBusinessEvent.event_uuid == removed.reference
            )
        )
        assert set(audit.after_state) == {"source_snapshot_digest", "retention_cutoff"}
