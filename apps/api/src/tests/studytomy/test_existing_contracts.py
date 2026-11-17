"""Baseline regression contract tests for TASK-001 (V-01).

Captures and pins the existing learner, instructor, and admin flows, JSON
response shapes, and Alembic migration head using synthetic fixtures only:
1. Authentication login flow and response contract (`POST /api/v1/auth/login`).
2. Course read flow and `CourseRead` response contract (`GET /api/v1/courses/{uuid}`).
3. Assignment authoring, learner task draft save, final submission, post-submit
   answer freeze, instructor grading, and destructive live retry behavior.
4. Own certificate read (`get_user_certificates_for_course`) surviving course
   unpublication, and certificate withholding when assignments are not passed.
5. Alembic migration head and core model registration.
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlmodel import select

from src.core.events.database import get_db_session
from src.core.migrations import head_revisions, load_models
from src.db.courses.assignments import (
    AssignmentCreate,
    AssignmentRead,
    AssignmentTaskCreate,
    AssignmentTaskSubmission,
    AssignmentTaskSubmissionUpdate,
    AssignmentTaskTypeEnum,
    AssignmentUserSubmission,
    AssignmentUserSubmissionStatus,
    GradingTypeEnum,
)
from src.db.courses.certifications import CertificateUser, Certifications
from src.db.trails import Trail
from src.db.user_audit_events import UserAuditEvent, UserAuditEventType
from src.db.users import AnonymousUser, User
from src.routers.auth import router as auth_router
from src.routers.courses.certifications import router as certifications_router
from src.routers.courses.courses import router as courses_router
from src.security.api_token_utils import get_authenticated_non_api_token_user
from src.security.auth import get_current_user
from src.security.features_utils.dependencies import require_courses_feature
from src.services.courses.activities.assignments import (
    create_assignment,
    create_assignment_submission,
    create_assignment_task,
    get_grade_assignment_submission,
    grade_assignment_submission,
    handle_assignment_task_submission,
    retry_assignment_submission,
)
from src.services.courses.certifications import (
    are_course_assignments_passed,
    get_user_certificates_for_course,
)

_PATCH_ASSIGN_RBAC = "src.services.courses.activities.assignments.check_resource_access"
_PATCH_ASSIGN_LIMITS = "src.services.courses.activities.assignments.check_limits_with_usage"
_PATCH_ASSIGN_INCREASE = "src.services.courses.activities.assignments.increase_feature_usage"
_PATCH_ASSIGN_ROLES = (
    "src.services.courses.activities.assignments.authorization_verify_based_on_roles"
)
_PATCH_ASSIGN_DISPATCH = "src.services.courses.activities.assignments.dispatch_webhooks"
_PATCH_ASSIGN_TRACK = "src.services.courses.activities.assignments.track"
_PATCH_ASSIGN_CERT = (
    "src.services.courses.activities.assignments."
    "check_course_completion_and_create_certificate"
)
_PATCH_TRAIL_PRESENCE = "src.services.courses.activities.assignments.check_trail_presence"


@pytest.fixture(autouse=True)
def _bind_audit_session_factory(request, monkeypatch):
    """Bind the isolated audit session factory (`src.core.events.database._async_session_factory`)
    to the per-test in-memory SQLite engine when a test uses the `db`/`engine` fixture,
    so `record_audit_event` writes to an initialized `user_audit_event` table instead of
    an uninitialized global in-memory SQLite connection.
    """
    if "engine" not in request.fixturenames and "db" not in request.fixturenames:
        return
    engine = request.getfixturevalue("engine")
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from sqlmodel.ext.asyncio.session import AsyncSession

    import src.core.events.database as db_events

    test_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(db_events, "_async_session_factory", test_factory)


class TestMigrationAndMetadataBaseline:
    """Pins the baseline Alembic head and recursive model registration."""

    def test_alembic_head_revision_is_pinned(self):
        heads = head_revisions()
        assert heads == ["72c1a8f0d4e6"]

    def test_core_models_registered_in_metadata(self):
        metadata = load_models()
        expected_tables = {
            "user",
            "organization",
            "role",
            "course",
            "activity",
            "assignment",
            "assignmenttask",
            "assignmentusersubmission",
            "assignmenttasksubmission",
            "certifications",
            "certificateuser",
            "user_audit_event",
            "st_relationship",
            "st_sharing_grant",
            "st_course_access_grant",
            "st_capacity_hold",
            "st_order",
            "st_order_line",
            "st_order_acceptance",
            "st_payment_attempt",
            "st_money_movement",
            "st_manual_collection_evidence",
            "st_collection_remedy",
            "st_collection_resolution",
            "st_curriculum_revision",
            "st_concept",
            "st_concept_prerequisite",
            "st_concept_activity",
            "st_item_revision",
            "st_diagnostic_revision",
            "st_learning_attempt",
            "st_attempt_item",
            "st_learning_evidence",
            "st_concept_mastery",
        }
        assert expected_tables.issubset(set(metadata.tables.keys()))


class TestExistingLoginContract:
    """Pins the existing `/api/v1/auth/login` response contract with synthetic user data."""

    async def test_login_returns_user_and_tokens_shape(self, db):
        synthetic_user = User(
            id=101,
            username="synthetic_learner",
            first_name="Synthetic",
            last_name="Learner",
            email="synthetic.learner@example.test",
            password="hashed_password_placeholder",
            user_uuid="user_synthetic_learner",
            email_verified=True,
            creation_date="2026-11-15T00:00:00Z",
            update_date="2026-11-15T00:00:00Z",
        )
        db.add(synthetic_user)
        await db.commit()
        await db.refresh(synthetic_user)

        app = FastAPI()
        app.include_router(auth_router, prefix="/api/v1/auth")
        app.dependency_overrides[get_db_session] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: AnonymousUser()

        with (
            patch("src.routers.auth.check_login_rate_limit", return_value=(True, None)),
            patch("src.routers.auth.check_account_locked", return_value=(False, None)),
            patch(
                "src.routers.auth.authenticate_user",
                new_callable=AsyncMock,
                return_value=synthetic_user,
            ),
            patch("src.routers.auth.reset_failed_attempts"),
            patch("src.routers.auth.get_client_ip", return_value="127.0.0.1"),
            patch("src.routers.auth.update_login_info"),
            patch(
                "src.services.auth.session.create_access_token",
                return_value="syn-access-jwt",
            ),
            patch(
                "src.services.auth.session.create_refresh_token",
                return_value="syn-refresh-jwt",
            ),
            patch("src.routers.auth.set_auth_cookies"),
            patch("src.routers.auth.get_deployment_mode", return_value="oss"),
            patch("src.routers.auth.get_token_expiry_ms", return_value=3600000),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/api/v1/auth/login",
                    data={
                        "username": synthetic_user.email,
                        "password": "synthetic-password",
                    },
                )

        assert resp.status_code == 200
        body = resp.json()
        assert set(body.keys()) >= {"user", "tokens"}
        assert body["user"]["user_uuid"] == "user_synthetic_learner"
        assert body["user"]["email"] == "synthetic.learner@example.test"
        assert "password" not in body["user"]
        assert body["tokens"]["access_token"] == "syn-access-jwt"
        assert body["tokens"]["refresh_token"] == "syn-refresh-jwt"
        assert body["tokens"]["expiry"] == 3600000


class TestExistingCourseReadContract:
    """Pins the existing course read service and router response shape."""

    async def test_course_read_returns_expected_contract(
        self, db, org, course, regular_user
    ):
        app = FastAPI()
        app.include_router(courses_router, prefix="/api/v1/courses")
        app.dependency_overrides[get_db_session] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: regular_user
        app.dependency_overrides[require_courses_feature] = lambda: True

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(f"/api/v1/courses/{course.course_uuid}")

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["course_uuid"] == course.course_uuid
        assert payload["name"] == "Test Course"
        assert payload["org_id"] == org.id
        assert payload["published"] is True
        assert payload["public"] is True
        assert isinstance(payload["authors"], list)


class TestExistingAssignmentLifecycleContract:
    """Pins the existing assignment authoring, draft save, submit, freeze, grade, and retry flow."""

    async def test_learner_save_submit_freeze_grade_and_retry_contract(
        self,
        mock_request,
        db,
        org,
        course,
        chapter,
        activity,
        admin_user,
        regular_user,
    ):
        # 1. Instructor/admin creates an assignment with retries enabled
        with (
            patch(_PATCH_ASSIGN_RBAC, new_callable=AsyncMock),
            patch(
                "src.services.courses.activities.assignments.authorize_assignment_access",
                new_callable=AsyncMock,
            ),
            patch(
                "src.services.courses.activities.assignments.index_activity",
            ),
            patch(_PATCH_ASSIGN_LIMITS, new_callable=AsyncMock),
            patch(_PATCH_ASSIGN_INCREASE, new_callable=AsyncMock),
        ):
            created_assignment = await create_assignment(
                mock_request,
                AssignmentCreate(
                    title="Baseline Algebra Check",
                    description="Synthetic baseline assessment",
                    due_date="2030-12-31",
                    grading_type=GradingTypeEnum.NUMERIC,
                    org_id=org.id,
                    course_id=course.id,
                    chapter_id=chapter.id,
                    activity_id=activity.id,
                ),
                admin_user,
                db,
            )
            assert isinstance(created_assignment, AssignmentRead)

            created_task = await create_assignment_task(
                mock_request,
                created_assignment.assignment_uuid,
                AssignmentTaskCreate(
                    title="Question 1",
                    description="Compute 6 * 7",
                    hint="",
                    reference_file=None,
                    assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
                    contents={
                        "prompt": "What is 6 * 7?",
                        "correct_answers": ["42"],
                        "match_mode": "exact",
                    },
                    max_grade_value=100,
                ),
                admin_user,
                db,
            )

        # Enable retries on the persisted Assignment row to verify baseline retry behavior
        from src.db.courses.assignments import Assignment

        assignment_row = (
            await db.execute(
                select(Assignment).where(
                    Assignment.assignment_uuid == created_assignment.assignment_uuid
                )
            )
        ).scalars().first()
        assert assignment_row is not None
        assignment_row.allow_retries = True
        assignment_row.max_retries = 2
        db.add(assignment_row)
        await db.commit()
        await db.refresh(assignment_row)

        # 2. Learner saves a draft task answer (is_instructor=False, enrolled=True)
        with (
            patch(_PATCH_ASSIGN_RBAC, new_callable=AsyncMock),
            patch(_PATCH_ASSIGN_ROLES, new_callable=AsyncMock, side_effect=[False, True]),
        ):
            saved_task = await handle_assignment_task_submission(
                mock_request,
                created_task.assignment_task_uuid,
                AssignmentTaskSubmissionUpdate(task_submission={"answer": "42"}),
                regular_user,
                db,
            )
        assert saved_task.user_id == regular_user.id
        assert saved_task.task_submission == {"answer": "42"}
        assert saved_task.grade == 0

        # 3. Learner submits the assignment
        trail = Trail(
            org_id=org.id,
            user_id=regular_user.id,
            trail_uuid="trail_baseline_contract",
            creation_date=str(datetime.now()),
            update_date=str(datetime.now()),
        )
        db.add(trail)
        await db.commit()
        await db.refresh(trail)

        with (
            patch(_PATCH_ASSIGN_RBAC, new_callable=AsyncMock),
            patch(
                "src.services.courses.activities.assignments.authorize_assignment_access",
                new_callable=AsyncMock,
            ),
            patch(_PATCH_ASSIGN_ROLES, new_callable=AsyncMock, return_value=False),
            patch(_PATCH_TRAIL_PRESENCE, new_callable=AsyncMock, return_value=trail),
            patch(_PATCH_ASSIGN_CERT, new_callable=AsyncMock),
            patch(_PATCH_ASSIGN_TRACK, new_callable=AsyncMock),
            patch(_PATCH_ASSIGN_DISPATCH, new_callable=AsyncMock),
        ):
            submission = await create_assignment_submission(
                mock_request,
                created_assignment.assignment_uuid,
                regular_user,
                db,
            )
        assert submission.submission_status == AssignmentUserSubmissionStatus.SUBMITTED
        assert submission.attempt_number == 1

        # 4. Post-submit answer freeze: learner cannot overwrite answers while SUBMITTED
        with (
            patch(_PATCH_ASSIGN_RBAC, new_callable=AsyncMock),
            patch(_PATCH_ASSIGN_ROLES, new_callable=AsyncMock, side_effect=[False, True]),
        ):
            with pytest.raises(HTTPException) as freeze_exc:
                await handle_assignment_task_submission(
                    mock_request,
                    created_task.assignment_task_uuid,
                    AssignmentTaskSubmissionUpdate(
                        assignment_task_submission_uuid=saved_task.assignment_task_submission_uuid,
                        task_submission={"answer": "99"},
                    ),
                    regular_user,
                    db,
                )
        assert freeze_exc.value.status_code == 403

        # 5. Instructor grades the submission; objective verifier scores "42" -> 100
        with (
            patch(_PATCH_ASSIGN_RBAC, new_callable=AsyncMock),
            patch(_PATCH_ASSIGN_ROLES, new_callable=AsyncMock, return_value=True),
            patch(_PATCH_ASSIGN_CERT, new_callable=AsyncMock),
            patch(_PATCH_ASSIGN_TRACK, new_callable=AsyncMock),
            patch(_PATCH_ASSIGN_DISPATCH, new_callable=AsyncMock),
        ):
            grade_result = await grade_assignment_submission(
                mock_request,
                regular_user.id,
                created_assignment.assignment_uuid,
                admin_user,
                db,
            )
            grade_view = await get_grade_assignment_submission(
                mock_request,
                regular_user.id,
                created_assignment.assignment_uuid,
                admin_user,
                db,
            )

        assert grade_result["grade"] == 100
        assert grade_result["max_grade"] == 100
        assert grade_result["percentage"] == 100.0
        assert grade_result["display_grade"] == "100/100"
        assert grade_result["passed"] is True
        assert "message" in grade_result
        assert "display_grade" in grade_view
        assert len(grade_view["tasks"]) == 1

        # Separately verify persisted AssignmentUserSubmission state after grading
        persisted_sub = (
            await db.execute(
                select(AssignmentUserSubmission).where(
                    AssignmentUserSubmission.user_id == regular_user.id,
                    AssignmentUserSubmission.assignment_id == assignment_row.id,
                )
            )
        ).scalars().first()
        assert persisted_sub is not None
        assert persisted_sub.submission_status == AssignmentUserSubmissionStatus.GRADED
        assert persisted_sub.grade == 100

        # Verify durable audit event rows were written cleanly without OperationalError
        audit_events = (
            await db.execute(
                select(UserAuditEvent).where(UserAuditEvent.user_id == regular_user.id)
            )
        ).scalars().all()
        audit_types = {ev.event_type for ev in audit_events}
        assert UserAuditEventType.ASSIGNMENT_SUBMITTED in audit_types
        assert UserAuditEventType.ASSIGNMENT_GRADED in audit_types

        # Seed a course certificate issued upon passing grade so we can verify
        # that retry_assignment_submission retains certificate revocation
        cert = Certifications(
            id=510,
            course_id=course.id,
            config={"certification_name": "Baseline Retry Revocation Check"},
            certification_uuid="certification_retry_510",
            creation_date="2026-11-15T00:00:00Z",
            update_date="2026-11-15T00:00:00Z",
        )
        db.add(cert)
        await db.commit()
        await db.refresh(cert)
        cert_user = CertificateUser(
            id=610,
            user_id=regular_user.id,
            certification_id=cert.id,
            user_certification_uuid="ST-RETRY-REVOKE-001",
            created_at="2026-11-15T00:00:00Z",
            updated_at="2026-11-15T00:00:00Z",
        )
        db.add(cert_user)
        await db.commit()

        # 6. Baseline retry behavior (F-03): live retry deletes task submission rows,
        # resets the single AssignmentUserSubmission row to PENDING with attempt_number=2,
        # and revokes any previously issued course certificate.
        with (
            patch(_PATCH_ASSIGN_RBAC, new_callable=AsyncMock),
            patch(_PATCH_ASSIGN_TRACK, new_callable=AsyncMock),
            patch(_PATCH_ASSIGN_DISPATCH, new_callable=AsyncMock),
            patch("src.services.courses.certifications.dispatch_webhooks", new_callable=AsyncMock),
            patch("src.services.courses.certifications.track", new_callable=AsyncMock),
        ):
            retried = await retry_assignment_submission(
                mock_request,
                created_assignment.assignment_uuid,
                regular_user,
                db,
            )

        assert retried["message"] == "Assignment User Submission reset for retry"
        assert retried["attempt_number"] == 2
        assert retried["max_retries"] == 2
        assert (
            retried["submission"]["submission_status"]
            == AssignmentUserSubmissionStatus.PENDING
        )
        assert retried["submission"]["attempt_number"] == 2
        assert retried["submission"]["grade"] == 0

        # Verify persisted AssignmentUserSubmission row state after retry
        await db.refresh(persisted_sub)
        assert persisted_sub.submission_status == AssignmentUserSubmissionStatus.PENDING
        assert persisted_sub.attempt_number == 2
        assert persisted_sub.grade == 0
        assert persisted_sub.overall_feedback is None

        # Verify task submission rows were deleted on live retry
        remaining_tasks = (
            await db.execute(
                select(AssignmentTaskSubmission).where(
                    AssignmentTaskSubmission.user_id == regular_user.id,
                    AssignmentTaskSubmission.assignment_task_id == created_task.id,
                )
            )
        ).scalars().all()
        assert remaining_tasks == []

        # Verify previously issued course certificate was revoked upon retry
        remaining_certs = (
            await db.execute(
                select(CertificateUser).where(
                    CertificateUser.user_id == regular_user.id,
                    CertificateUser.certification_id == cert.id,
                )
            )
        ).scalars().all()
        assert remaining_certs == []


class TestExistingCertificateReadContract:
    """Pins the existing own-certificate read and assignment-pass eligibility check."""

    async def test_own_certificate_readable_when_course_unpublished(
        self, mock_request, db, course, regular_user
    ):
        cert = Certifications(
            id=501,
            course_id=course.id,
            config={"certification_name": "Baseline Completion Award"},
            certification_uuid="certification_baseline_501",
            creation_date="2026-11-15T00:00:00Z",
            update_date="2026-11-15T00:00:00Z",
        )
        db.add(cert)
        await db.commit()
        await db.refresh(cert)

        cert_user = CertificateUser(
            id=601,
            user_id=regular_user.id,
            certification_id=cert.id,
            user_certification_uuid="ST-BASELINE-CERT-001",
            created_at="2026-11-15T00:00:00Z",
            updated_at="2026-11-15T00:00:00Z",
        )
        db.add(cert_user)

        # Unpublish and make course private after award issuance
        course.published = False
        course.public = False
        db.add(course)
        await db.commit()

        # Service-level check: learner still reads their own certificate
        records = await get_user_certificates_for_course(
            mock_request, course.course_uuid, regular_user, db
        )
        assert len(records) == 1
        assert (
            records[0]["certificate_user"].user_certification_uuid
            == "ST-BASELINE-CERT-001"
        )
        assert records[0]["user"]["user_uuid"] == regular_user.user_uuid

        # Router-level check
        app = FastAPI()
        app.include_router(certifications_router, prefix="/api/v1/certifications")
        app.dependency_overrides[get_db_session] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: regular_user
        app.dependency_overrides[get_authenticated_non_api_token_user] = (
            lambda: regular_user
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get(
                f"/api/v1/certifications/user/course/{course.course_uuid}"
            )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert (
            body[0]["certificate_user"]["user_certification_uuid"]
            == "ST-BASELINE-CERT-001"
        )

    async def test_assignments_passed_gate_blocks_ungraded_or_failed_work(
        self, db, org, course, chapter, activity, regular_user
    ):
        from src.db.courses.assignments import Assignment, AssignmentTask

        assignment = Assignment(
            id=701,
            title="Required Gate Assignment",
            description="Must pass",
            due_date="2030-01-01",
            published=True,
            grading_type=GradingTypeEnum.NUMERIC,
            org_id=org.id,
            course_id=course.id,
            chapter_id=chapter.id,
            activity_id=activity.id,
            assignment_uuid="assignment_gate_701",
            creation_date="2026-11-15T00:00:00Z",
            update_date="2026-11-15T00:00:00Z",
        )
        db.add(assignment)
        await db.commit()

        # Attach a gradable AssignmentTask (max_grade_value=100) so the assignment
        # has positive gradable points and is not treated as vacuously passed.
        task = AssignmentTask(
            id=702,
            title="Gate Question 1",
            description="Gradable question",
            hint="",
            reference_file=None,
            assignment_type=AssignmentTaskTypeEnum.SHORT_ANSWER,
            contents={"prompt": "2 + 2", "correct_answers": ["4"]},
            max_grade_value=100,
            order=0,
            assignment_id=assignment.id,
            activity_id=activity.id,
            course_id=course.id,
            chapter_id=chapter.id,
            org_id=org.id,
            assignment_task_uuid="assignmenttask_gate_702",
            creation_date="2026-11-15T00:00:00Z",
            update_date="2026-11-15T00:00:00Z",
        )
        db.add(task)
        await db.commit()

        # No submission -> False
        assert (
            await are_course_assignments_passed(regular_user.id, course.id, db)
            is False
        )

        # Submitted but not graded -> False
        sub = AssignmentUserSubmission(
            id=801,
            user_id=regular_user.id,
            assignment_id=assignment.id,
            grade=0,
            submission_status=AssignmentUserSubmissionStatus.SUBMITTED,
            attempt_number=1,
            assignmentusersubmission_uuid="aus_gate_801",
            creation_date="2026-11-15T00:00:00Z",
            update_date="2026-11-15T00:00:00Z",
        )
        db.add(sub)
        await db.commit()
        assert (
            await are_course_assignments_passed(regular_user.id, course.id, db)
            is False
        )

        # Graded with failing score (20 < 50% threshold) -> False
        sub.submission_status = AssignmentUserSubmissionStatus.GRADED
        sub.grade = 20
        db.add(sub)
        await db.commit()
        assert (
            await are_course_assignments_passed(regular_user.id, course.id, db)
            is False
        )

        # Graded with passing score (80 >= 50% threshold) -> True
        sub.grade = 80
        db.add(sub)
        await db.commit()
        assert (
            await are_course_assignments_passed(regular_user.id, course.id, db)
            is True
        )
