"""Regression tests for managed virtual-lab submissions, release consistency, and build pinning."""

from __future__ import annotations

import asyncio
import io
import zipfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from starlette.datastructures import Headers

from src.db.courses.activities import Activity, ActivityTypeEnum
from src.db.courses.assignments import (
    Assignment,
    AssignmentTask,
    AssignmentTaskSubmission,
    AssignmentTaskTypeEnum,
    AssignmentUserSubmission,
    AssignmentUserSubmissionStatus,
    GradingTypeEnum,
)
from src.db.courses.chapter_activities import ChapterActivity
from src.db.courses.chapters import Chapter
from src.db.courses.course_chapters import CourseChapter
from src.db.courses.courses import Course
from src.db.studytomy.access import StudyCourseAccessGrant
from src.db.studytomy.adaptive_evidence import StudyLearningEvidence
from src.db.studytomy.assessment_archive import StudyAssessmentResponseArchive
from src.db.studytomy.education import StudyCourseRelease, StudyCourseReview
from src.db.studytomy.family import (
    StudyEligibilityDecision,
    StudyLearnerControlMode,
    StudyLearnerProfile,
    StudyLearnerProfileStatus,
)
from src.db.studytomy.governance import (
    StudyOwnerFunction,
    StudyPolicyKey,
)
from src.db.studytomy.operations import StudyEvidenceFile
from src.db.studytomy.privacy import StudyConsentEvent
from src.db.users import PublicUser, User
from src.routers.studytomy.commerce import ManagedTaskDraftRequest
from src.services.courses.activities.assignments import (
    AssignmentTaskSubmissionUpdate,
    delete_assignment_task,
    get_grade_assignment_submission,
    grade_assignment_submission,
    handle_assignment_task_submission,
    read_assignment_submissions,
)
from src.services.studytomy.access_policy import (
    read_managed_linked_assignment,
    read_managed_submission_file_download,
    read_managed_virtual_lab,
    retry_managed_linked_assignment,
    save_managed_assignment_task_draft,
    submit_managed_linked_assignment,
    upload_managed_assignment_task_file,
)
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.releases import (
    course_candidate_snapshot,
    validate_release_media_coverage,
)
from src.services.studytomy.virtual_labs import (
    validate_governed_virtual_lab_content,
)
from src.tests.studytomy.test_current_head_audit_remediations import _seed_policies_with_approvals
from src.tests.studytomy.test_virtual_lab_and_platform_audit import (
    _to_public_user,
    _valid_lab_content,
)


def _mock_request(user_id: int) -> MagicMock:
    req = MagicMock(spec=Request)
    req.headers = {}
    req.client = MagicMock(host="127.0.0.1")
    req.state = MagicMock()
    req.state.session_provenance = None
    return req


def _zip_upload(filename: str = "momentum-readings.zip") -> UploadFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("run-1.csv", "# Run ID: 1\n# mass1=2,mass2=2,v1=2.5,v2=0,e=1\nt,total_p_x\n0,5.0\n")
    buf.seek(0)
    return UploadFile(
        file=buf,
        filename=filename,
        headers=Headers({"content-type": "application/zip"}),
    )


async def _seed_pilot_environment(
    db: AsyncSession,
    org,
    admin_user: User,
    regular_user: User,
    *,
    due_date: str = "2099-12-31",
    allow_retries: bool = False,
    max_retries: int = 0,
    tag: str = "pilot",
):
    now = datetime.now(timezone.utc)
    course = Course(
        org_id=org.id,
        course_uuid=f"course_{tag}_momentum",
        name="Grade 12 G.C.E. A/L Physics — Conservation of Momentum",
        description="NIE / MoE Grade 12 Mechanics Pilot",
        about="",
        learnings="",
        tags="",
        thumbnail_type="image",
        thumbnail_image="",
        public=False,
        published=False,
        open_to_contributors=False,
        studytomy_managed=True,
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(course)
    await db.flush()

    chapter = Chapter(
        org_id=org.id,
        course_id=course.id,
        chapter_uuid=f"chap_{tag}_1",
        name="Conservation of Linear Momentum",
        description="",
        thumbnail_image="",
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(chapter)
    await db.flush()
    db.add(CourseChapter(org_id=org.id, course_id=course.id, chapter_id=chapter.id, order=1, creation_date="2026-01-01", update_date="2026-01-01"))

    asg_act = Activity(
        org_id=org.id,
        course_id=course.id,
        activity_uuid=f"act_{tag}_asg",
        name="Momentum Practical Report",
        activity_type=ActivityTypeEnum.TYPE_ASSIGNMENT,
        activity_sub_type="SUBTYPE_ASSIGNMENT_ANY",
        content={},
        details={},
        published=True,
        current_version=1,
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(asg_act)
    await db.flush()

    lab_act = Activity(
        org_id=org.id,
        course_id=course.id,
        activity_uuid=f"act_{tag}_lab",
        name="Virtual Lab: Conservation of Linear Momentum",
        activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
        activity_sub_type="SUBTYPE_DYNAMIC_EMBED",
        content=_valid_lab_content(linked_assignment_activity_uuid=asg_act.activity_uuid),
        details={},
        published=True,
        current_version=1,
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(lab_act)
    await db.flush()

    db.add(ChapterActivity(org_id=org.id, course_id=course.id, chapter_id=chapter.id, activity_id=lab_act.id, order=1, creation_date="2026-01-01", update_date="2026-01-01"))
    db.add(ChapterActivity(org_id=org.id, course_id=course.id, chapter_id=chapter.id, activity_id=asg_act.id, order=2, creation_date="2026-01-01", update_date="2026-01-01"))

    assignment = Assignment(
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=asg_act.id,
        assignment_uuid=f"asg_{tag}_1",
        title="Momentum Practical Assessment",
        description="Submit calculated velocities and a ZIP/PDF practical report.",
        due_date=due_date,
        published=True,
        grading_type=GradingTypeEnum.NUMERIC,
        allow_retries=allow_retries,
        max_retries=max_retries,
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(assignment)
    await db.flush()

    task_num = AssignmentTask(
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=asg_act.id,
        assignment_id=assignment.id,
        assignment_task_uuid=f"task_{tag}_num",
        title="Equal-mass Elastic Collision v2f",
        description="Calculate body 2 final velocity (m/s).",
        hint="",
        assignment_type=AssignmentTaskTypeEnum.NUMBER_ANSWER,
        contents={"prompt": "Find v2f in m/s", "unit": "m/s", "correct_value": 2.5, "tolerance": 0.05},
        max_grade_value=2,
        order=0,
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    task_file = AssignmentTask(
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=asg_act.id,
        assignment_id=assignment.id,
        assignment_task_uuid=f"task_{tag}_file",
        title="Practical Report & Exported Runs (PDF/ZIP)",
        description="Upload ZIP with CSV runs or PDF report.",
        hint="",
        assignment_type=AssignmentTaskTypeEnum.FILE_SUBMISSION,
        contents={"instructions": "Package CSV runs in ZIP."},
        max_grade_value=8,
        order=1,
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(task_num)
    db.add(task_file)
    await db.flush()

    policies, owners = await _seed_policies_with_approvals(
        db,
        org,
        admin_user.id,
        {
            StudyPolicyKey.D_01: {"markets": ["LK"], "learner_age_limits": {"grades": "6-13"}, "independent_participation": {"verified": True}},
            StudyPolicyKey.D_02: {"subjects": ["Physics"], "learner_levels": ["Grade 12"], "languages": ["en"], "curriculum_claims": {"adaptive_curriculum_keys": ["LK-PHY-G12"]}},
            StudyPolicyKey.D_03: {"guardian_authority": {"verification": "human"}, "adult_transition": {"review": "human"}},
            StudyPolicyKey.D_05: {"prices": {"currency": "LKR"}, "access_periods": {"term_days": 90}, "learner_allocation": {"single_named_learner": True}},
            StudyPolicyKey.D_08: {"assessment": {"passing_score": 50}, "assistance": {"adaptive_guidance": {"enabled": True}}},
            StudyPolicyKey.D_09: {
                "release_schedule": {
                    "mode": "frozen",
                    "ungated_lab_activities": {course.course_uuid: [lab_act.activity_uuid]},
                },
                "completion_prerequisites": {"required": False},
                "mastery_gate": {"separate": True},
            },
            StudyPolicyKey.D_11: {"community_audiences": {"restricted": True}, "adult_contact_boundaries": {"moderated": True}, "moderation": {"enabled": True}},
            StudyPolicyKey.D_12: {"information_uses": {"learning_terms": ["course_release"]}, "consent": {"notice_versions": {"learning_terms": "v1"}, "notice_texts": {"learning_terms": "Approved"}}, "parent_report_scope": ["consent_management"]},
            StudyPolicyKey.D_13: {
                "retention": {"learner_evidence_retention_category": "education_7y", "managed_assessment_archive_days": 365},
                "record_access": {"assigned_reviewer_only": True},
            },
            StudyPolicyKey.D_15: {"accessibility_standard": {"wcag": "2.1-AA"}, "supported_formats": {"media_types": ["application/pdf"], "max_bytes": 10_000_000}, "service_recovery": {"fallback": "draft"}},
            StudyPolicyKey.D_16: {"content_rights": {"verified": True}, "review_approvals": {"independent_reviewer": True}, "certificate_presentation": {"none": True}},
            StudyPolicyKey.D_18: {"customer_terms": {"version": "v1"}, "selected_market_requirements": {"jurisdiction": "LK"}},
        },
    )
    edu_owner = owners[StudyOwnerFunction.EDUCATION]

    snapshot = await course_candidate_snapshot(db, course)
    validate_release_media_coverage(snapshot)
    sha = canonical_request_hash(snapshot)
    rights_ev = StudyEvidenceFile(
        evidence_uuid=f"ev_rights_{tag}_{sha[:8]}",
        org_id=org.id,
        course_uuid=course.course_uuid,
        revision_sha256=sha,
        purpose="content_rights",
        storage_reference=f"mem://rights_{tag}_{sha[:8]}.pdf",
        content_sha256="1" * 64,
        policy_version_ids=[int(policies[StudyPolicyKey.D_16].id)],
        media_type="application/pdf",
        size_bytes=128,
        retention_category="education_7y",
        upload_status="ready",
        uploaded_by_user_id=admin_user.id,
    )
    access_ev = StudyEvidenceFile(
        evidence_uuid=f"ev_access_{tag}_{sha[:8]}",
        org_id=org.id,
        course_uuid=course.course_uuid,
        revision_sha256=sha,
        purpose="content_accessibility",
        storage_reference=f"mem://access_{tag}_{sha[:8]}.pdf",
        content_sha256="2" * 64,
        policy_version_ids=[int(policies[StudyPolicyKey.D_15].id)],
        media_type="application/pdf",
        size_bytes=128,
        retention_category="education_7y",
        upload_status="ready",
        uploaded_by_user_id=admin_user.id,
    )
    db.add_all([rights_ev, access_ev])
    await db.flush()
    review = StudyCourseReview(
        org_id=org.id,
        course_uuid=course.course_uuid,
        revision_sha256=sha,
        owner_user_id=regular_user.id,
        reviewer_user_id=admin_user.id,
        reviewer_assignment_id=edu_owner.id,
        rights_evidence_id=rights_ev.id,
        accessibility_evidence_id=access_ev.id,
        policy_version_ids=[int(policies[StudyPolicyKey.D_09].id)],
        decision="approved",
        accessibility_confirmed=True,
        assessment_confirmed=True,
    )
    db.add(review)
    await db.flush()
    release = StudyCourseRelease(
        org_id=org.id,
        course_uuid=course.course_uuid,
        review_id=review.id,
        assessment_policy_id=policies[StudyPolicyKey.D_08].id,
        progression_policy_id=policies[StudyPolicyKey.D_09].id,
        retention_policy_id=policies[StudyPolicyKey.D_13].id,
        rights_policy_id=policies[StudyPolicyKey.D_16].id,
        retention_category="education_7y",
        policy_binding={"policy_version_ids": [int(policies[StudyPolicyKey.D_09].id)]},
        snapshot=snapshot,
        snapshot_sha256=sha,
        media_manifest={},
        released_by_user_id=admin_user.id,
    )
    db.add(release)
    await db.flush()

    profile = StudyLearnerProfile(
        org_id=org.id,
        user_id=regular_user.id,
        learner_uuid=f"lrn_{tag}_1",
        created_by_user_id=admin_user.id,
        status=StudyLearnerProfileStatus.ACTIVE.value,
        control_mode=StudyLearnerControlMode.INDEPENDENT.value,
    )
    db.add(profile)
    await db.flush()
    el_policy_ids = [int(policies[k].id) for k in (StudyPolicyKey.D_01, StudyPolicyKey.D_02, StudyPolicyKey.D_12)]
    indep_policy_ids = [int(policies[k].id) for k in (StudyPolicyKey.D_01, StudyPolicyKey.D_03, StudyPolicyKey.D_12)]
    c_policy_ids = [int(policies[k].id) for k in (StudyPolicyKey.D_01, StudyPolicyKey.D_03, StudyPolicyKey.D_12, StudyPolicyKey.D_05, StudyPolicyKey.D_18)]
    db.add(StudyEligibilityDecision(
        org_id=org.id, learner_profile_id=profile.id, eligibility_kind="learning",
        outcome="eligible", evidence_reference="ELIG-P1", policy_version_ids=el_policy_ids,
        reviewer_user_id=admin_user.id, owner_assignment_id=edu_owner.id, effective_from=now - timedelta(hours=1),
    ))
    db.add(StudyEligibilityDecision(
        org_id=org.id, learner_profile_id=profile.id, eligibility_kind="independent_participation",
        outcome="eligible", evidence_reference="ELIG-INDEP-P1", policy_version_ids=indep_policy_ids,
        reviewer_user_id=admin_user.id, owner_assignment_id=edu_owner.id, effective_from=now - timedelta(hours=1),
    ))
    consent = StudyConsentEvent(
        org_id=org.id, learner_profile_id=profile.id, actor_user_id=regular_user.id,
        subject_user_id=regular_user.id, purpose_code="learning_terms", choice="accept",
        notice_version="v1", scope_codes=["course_release"], policy_version_ids=c_policy_ids,
        effective_at=now - timedelta(hours=1),
    )
    db.add(consent)
    grant = StudyCourseAccessGrant(
        org_id=org.id, learner_profile_id=profile.id, course_uuid=course.course_uuid,
        release_id=release.id, source_kind="order_line", source_uuid="ord_pilot_1",
        start_basis="fixed_period", starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(days=30), created_at=now - timedelta(hours=1),
    )
    db.add(grant)
    await db.commit()
    return {
        "course": course, "release": release, "lab_act": lab_act, "asg_act": asg_act,
        "assignment": assignment, "task_num": task_num, "task_file": task_file,
        "profile": profile, "grant": grant, "consent": consent, "policies": policies,
        "edu_owner": edu_owner,
    }


@pytest.mark.asyncio
async def test_managed_submission_grading_and_idempotency_lifecycle(db, org, admin_user, regular_user, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seeded = await _seed_pilot_environment(db, org, admin_user, regular_user)
    course = seeded["course"]
    release = seeded["release"]
    asg_act = seeded["asg_act"]
    assignment = seeded["assignment"]
    task_num = seeded["task_num"]
    task_file = seeded["task_file"]
    learner = _to_public_user(regular_user)
    teacher = _to_public_user(admin_user)

    # 1. Read assignment BEFORE submission: answer keys are stripped, editable
    initial = await read_managed_linked_assignment(db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid)
    assert initial.submission_status is None
    assert initial.can_submit is True
    assert "correct_value" not in initial.model_dump_json()

    # 2. Save draft numeric answer
    after_num = await save_managed_assignment_task_draft(
        db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid,
        task_num.assignment_task_uuid, {"answer": "2.5"}, idempotency_key="test",
    )
    assert after_num.submission_status == "PENDING"
    assert after_num.tasks[0].submitted_answer == {"answer": "2.5"}

    # 3. Upload ZIP report containing exported CSV and attach to draft
    upload_receipt = await upload_managed_assignment_task_file(
        db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid,
        task_file.assignment_task_uuid, _zip_upload(), idempotency_key="upload-zip-1",
    )
    assert upload_receipt.file_uuid.endswith(".zip")
    assert f"_submission_u{learner.id}." in upload_receipt.file_uuid

    after_file = await save_managed_assignment_task_draft(
        db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid,
        task_file.assignment_task_uuid, {"fileUUID": upload_receipt.file_uuid}, idempotency_key="draft-file-1",
    )
    assert after_file.tasks[1].submitted_answer == {"fileUUID": upload_receipt.file_uuid}

    # Learner can download own uploaded report
    dl = await read_managed_submission_file_download(
        db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid,
        task_file.assignment_task_uuid, upload_receipt.file_uuid,
    )
    assert dl.filename == upload_receipt.file_uuid

    # 4. Submit with Idempotency-Key -> stays SUBMITTED (manual FILE_SUBMISSION task required)
    submitted = await submit_managed_linked_assignment(
        db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid,
        idempotency_key="submit-round-1",
    )
    assert submitted.submission_status == "SUBMITTED"
    assert submitted.can_submit is False
    assert submitted.submission_uuid is not None

    # Replaying with SAME Idempotency-Key returns same projection safely
    replayed = await submit_managed_linked_assignment(
        db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid,
        idempotency_key="submit-round-1",
    )
    assert replayed.submission_uuid == submitted.submission_uuid
    assert replayed.submission_status == "SUBMITTED"

    # Submitting AGAIN with a DIFFERENT Idempotency-Key is rejected cleanly (409)
    with pytest.raises(HTTPException) as dup_exc:
        await submit_managed_linked_assignment(
            db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid,
            idempotency_key="submit-round-2",
        )
    assert dup_exc.value.status_code == 409

    # 5. Teacher marks manual FILE_SUBMISSION task and finalizes assignment grade
    task_file_sub = (
        await db.execute(
            select(AssignmentTaskSubmission).where(
                AssignmentTaskSubmission.assignment_task_id == task_file.id,
                AssignmentTaskSubmission.user_id == learner.id,
            )
        )
    ).scalar_one()
    with patch("src.services.courses.activities.assignments.authorize_assignment_access", new_callable=AsyncMock), \
         patch("src.services.courses.activities.assignments.check_resource_access", new_callable=AsyncMock), \
         patch("src.services.courses.activities.assignments._is_assignment_instructor", new_callable=AsyncMock, return_value=True):
        await handle_assignment_task_submission(
            _mock_request(teacher.id),
            task_file.assignment_task_uuid,
            AssignmentTaskSubmissionUpdate(
                assignment_task_submission_uuid=task_file_sub.assignment_task_submission_uuid,
                grade=7,
                task_submission_grade_feedback="Accurate elastic and inelastic momentum tables.",
                manually_graded=True,
            ),
            teacher,
            db,
        )
        graded = await grade_assignment_submission(
            _mock_request(teacher.id),
            learner.id,
            assignment.assignment_uuid,
            teacher,
            db,
            overall_feedback="Strong Grade 12 practical report.",
        )
    assert graded["grade"] == 9  # 2 (auto server-verified) + 7 (manual)
    assert graded["max_grade"] == 10

    # 6. Learner sees GRADED status, teacher feedback, and NO automatic mastery evidence
    final_view = await read_managed_linked_assignment(
        db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid
    )
    assert final_view.submission_status == "GRADED"
    assert final_view.grade == 9
    assert final_view.max_grade == 10
    assert final_view.overall_feedback == "Strong Grade 12 practical report."
    assert final_view.tasks[0].student_grade == 2
    assert final_view.tasks[1].student_grade == 7
    assert final_view.tasks[1].teacher_feedback == "Accurate elastic and inelastic momentum tables."

    mastery_rows = (await db.execute(select(StudyLearningEvidence).where(StudyLearningEvidence.eligible.is_(True)))).scalars().all()
    assert mastery_rows == []


@pytest.mark.asyncio
async def test_release_snapshot_isolation_and_consistency_after_draft_edits(db, org, admin_user, regular_user):
    seeded = await _seed_pilot_environment(db, org, admin_user, regular_user)
    course = seeded["course"]
    rel_v1 = seeded["release"]
    asg_act = seeded["asg_act"]
    assignment = seeded["assignment"]
    task_num = seeded["task_num"]
    task_file = seeded["task_file"]
    policies = seeded["policies"]
    edu_owner = seeded["edu_owner"]
    learner = _to_public_user(regular_user)
    teacher = _to_public_user(admin_user)

    # Learner submits Release v1 BEFORE author edits live draft
    await save_managed_assignment_task_draft(
        db, org, learner, course.course_uuid, rel_v1.uuid, asg_act.activity_uuid,
        task_num.assignment_task_uuid, {"answer": "2.5"},
    )
    fake_file = f"rep_submission_u{learner.id}.zip"
    await save_managed_assignment_task_draft(
        db, org, learner, course.course_uuid, rel_v1.uuid, asg_act.activity_uuid,
        task_file.assignment_task_uuid, {"fileUUID": fake_file},
    )
    await submit_managed_linked_assignment(
        db, org, learner, course.course_uuid, rel_v1.uuid, asg_act.activity_uuid,
        idempotency_key="test",
    )

    # Author edits LIVE draft assignment + tasks (simulating preparation for Release v2)
    assignment.grading_type = GradingTypeEnum.PERCENTAGE
    task_num.contents = {"prompt": "Changed", "unit": "m/s", "correct_value": 999.0, "tolerance": 0.01}
    task_num.max_grade_value = 50
    task_file.max_grade_value = 50
    db.add(assignment)
    db.add(task_num)
    db.add(task_file)
    await db.commit()

    # Deleting a retained task must fail closed with 409 MANAGED_ASSESSMENT_SOURCE_RETAINED
    with patch("src.services.courses.activities.assignments.authorize_assignment_access", new_callable=AsyncMock):
        with pytest.raises(HTTPException) as del_exc:
            await delete_assignment_task(_mock_request(teacher.id), task_num.assignment_task_uuid, teacher, db)
    assert del_exc.value.status_code == 409
    assert del_exc.value.detail["code"] == "MANAGED_ASSESSMENT_SOURCE_RETAINED"

    # Teacher tries to award 12 points on FILE_SUBMISSION (Release v1 max is 8, even though live draft v2 max is 50) -> 422!
    task_file_sub = (
        await db.execute(
            select(AssignmentTaskSubmission).where(
                AssignmentTaskSubmission.assignment_task_id == task_file.id,
                AssignmentTaskSubmission.user_id == learner.id,
            )
        )
    ).scalar_one()
    with patch("src.services.courses.activities.assignments.authorize_assignment_access", new_callable=AsyncMock), \
         patch("src.services.courses.activities.assignments.check_resource_access", new_callable=AsyncMock), \
         patch("src.services.courses.activities.assignments._is_assignment_instructor", new_callable=AsyncMock, return_value=True):
        with pytest.raises(HTTPException) as over_max_exc:
            await handle_assignment_task_submission(
                _mock_request(teacher.id),
                task_file.assignment_task_uuid,
                AssignmentTaskSubmissionUpdate(
                    assignment_task_submission_uuid=task_file_sub.assignment_task_submission_uuid,
                    grade=12,
                    manually_graded=True,
                ),
                teacher,
                db,
            )
        assert over_max_exc.value.status_code == 422

        # Teacher grades validly within Release v1 rubric (8/8) + finalize
        await handle_assignment_task_submission(
            _mock_request(teacher.id),
            task_file.assignment_task_uuid,
            AssignmentTaskSubmissionUpdate(
                assignment_task_submission_uuid=task_file_sub.assignment_task_submission_uuid,
                grade=8,
                manually_graded=True,
                task_submission_grade_feedback="Complete.",
            ),
            teacher,
            db,
        )
        graded = await grade_assignment_submission(
            _mock_request(teacher.id), learner.id, assignment.assignment_uuid, teacher, db,
            overall_feedback="Graded accurately against Release v1.",
        )

    # Release v1 answer key (2.5 -> 2/2) & max_grade=10 & grading_type=NUMERIC MUST win over draft edits!
    assert graded["grade"] == 10
    assert graded["max_grade"] == 10
    assert graded["grading_type"] == "NUMERIC"

    # Native read_assignment_submissions & get_grade_assignment_submission still verify cleanly without 409!
    with patch("src.services.courses.activities.assignments.authorize_assignment_access", new_callable=AsyncMock), \
         patch("src.services.courses.activities.assignments._is_assignment_instructor", new_callable=AsyncMock, return_value=True):
        sub_list = await read_assignment_submissions(_mock_request(teacher.id), assignment.assignment_uuid, teacher, db)
        grade_read = await get_grade_assignment_submission(_mock_request(teacher.id), learner.id, assignment.assignment_uuid, teacher, db)
    assert len(sub_list) == 1
    assert sub_list[0]["submission_status"] == AssignmentUserSubmissionStatus.GRADED
    assert sub_list[0]["grade_display"]["grade"] == 10
    assert grade_read["grade"] == 10

    # Publish Release v2 and grant learner access: Release v2 must isolate & reject conflicting write
    snap_v2 = await course_candidate_snapshot(db, course)
    sha_v2 = canonical_request_hash(snap_v2)
    review_v2 = StudyCourseReview(
        org_id=org.id, course_uuid=course.course_uuid, revision_sha256=sha_v2,
        owner_user_id=regular_user.id, reviewer_user_id=admin_user.id, reviewer_assignment_id=edu_owner.id,
        rights_evidence_id=1, accessibility_evidence_id=2, policy_version_ids=[int(policies[StudyPolicyKey.D_09].id)],
        decision="approved", accessibility_confirmed=True, assessment_confirmed=True,
    )
    db.add(review_v2)
    await db.flush()
    rel_v2 = StudyCourseRelease(
        org_id=org.id, course_uuid=course.course_uuid, review_id=review_v2.id,
        assessment_policy_id=policies[StudyPolicyKey.D_08].id,
        progression_policy_id=policies[StudyPolicyKey.D_09].id,
        retention_policy_id=policies[StudyPolicyKey.D_13].id,
        rights_policy_id=policies[StudyPolicyKey.D_16].id,
        retention_category="education_7y", policy_binding={"policy_version_ids": [int(policies[StudyPolicyKey.D_09].id)]},
        snapshot=snap_v2, snapshot_sha256=sha_v2, media_manifest={}, released_by_user_id=admin_user.id,
    )
    db.add(rel_v2)
    await db.flush()
    now = datetime.now(timezone.utc)
    db.add(StudyCourseAccessGrant(
        org_id=org.id, learner_profile_id=seeded["profile"].id, course_uuid=course.course_uuid,
        release_id=rel_v2.id, source_kind="order_line", source_uuid="ord_pilot_v2",
        start_basis="fixed_period", starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(days=30), created_at=now - timedelta(hours=1),
    ))
    await db.commit()

    v2_view = await read_managed_linked_assignment(db, org, learner, course.course_uuid, rel_v2.uuid, asg_act.activity_uuid)
    assert v2_view.release_conflict_uuid == rel_v1.uuid
    assert v2_view.submission_status is None
    assert v2_view.grade is None
    assert v2_view.can_submit is False

    with pytest.raises(HTTPException) as conflict_exc:
        await save_managed_assignment_task_draft(
            db, org, learner, course.course_uuid, rel_v2.uuid, asg_act.activity_uuid,
            task_num.assignment_task_uuid, {"answer": "999"},
        )
    assert conflict_exc.value.status_code == 409


@pytest.mark.asyncio
async def test_managed_submission_negative_boundaries_and_retries(db, org, admin_user, regular_user):
    seeded = await _seed_pilot_environment(
        db, org, admin_user, regular_user, allow_retries=True, max_retries=2
    )
    course = seeded["course"]
    release = seeded["release"]
    asg_act = seeded["asg_act"]
    assignment = seeded["assignment"]
    task_num = seeded["task_num"]
    task_file = seeded["task_file"]
    learner = _to_public_user(regular_user)

    # Extra fields forbidden by strict schema (no client grade/feedback injection)
    with pytest.raises(Exception):
        ManagedTaskDraftRequest.model_validate({"task_submission": {"answer": "2.5"}, "grade": 100})

    # Non-numeric answer and raw CSV / cross-learner fileUUID rejected
    with pytest.raises(HTTPException) as bad_num:
        await save_managed_assignment_task_draft(
            db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid,
            task_num.assignment_task_uuid, {"answer": "not-a-number"},
        )
    assert bad_num.value.status_code == 422

    with pytest.raises(HTTPException) as csv_exc:
        await save_managed_assignment_task_draft(
            db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid,
            task_file.assignment_task_uuid, {"fileUUID": f"bad_submission_u{learner.id}.csv"},
        )
    assert csv_exc.value.status_code == 415

    with pytest.raises(HTTPException) as other_owner:
        await save_managed_assignment_task_draft(
            db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid,
            task_file.assignment_task_uuid, {"fileUUID": "other_submission_u99999.zip"},
        )
    assert other_owner.value.status_code == 403

    # Incomplete submit rejected (422)
    await save_managed_assignment_task_draft(
        db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid,
        task_num.assignment_task_uuid, {"answer": "2.5"},
    )
    with pytest.raises(HTTPException) as incomplete_exc:
        await submit_managed_linked_assignment(
            db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid,
        )
    assert incomplete_exc.value.status_code == 422

    # Complete & submit, grade, then test Retry
    await save_managed_assignment_task_draft(
        db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid,
        task_file.assignment_task_uuid, {"fileUUID": f"ok_submission_u{learner.id}.pdf"},
    )
    await submit_managed_linked_assignment(
        db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid,
    )
    teacher = _to_public_user(admin_user)
    task_file_sub = (
        await db.execute(
            select(AssignmentTaskSubmission).where(
                AssignmentTaskSubmission.assignment_task_id == task_file.id,
                AssignmentTaskSubmission.user_id == learner.id,
            )
        )
    ).scalar_one()
    with patch("src.services.courses.activities.assignments.authorize_assignment_access", new_callable=AsyncMock), \
         patch("src.services.courses.activities.assignments.check_resource_access", new_callable=AsyncMock), \
         patch("src.services.courses.activities.assignments._is_assignment_instructor", new_callable=AsyncMock, return_value=True):
        await handle_assignment_task_submission(
            _mock_request(teacher.id),
            task_file.assignment_task_uuid,
            AssignmentTaskSubmissionUpdate(
                assignment_task_submission_uuid=task_file_sub.assignment_task_submission_uuid,
                grade=6,
                manually_graded=True,
            ),
            teacher,
            db,
        )
        await grade_assignment_submission(
            _mock_request(teacher.id), learner.id, assignment.assignment_uuid, teacher, db,
        )

    retried = await retry_managed_linked_assignment(
        db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid
    )
    assert retried.submission_status == "PENDING"
    assert retried.attempt_number == 2
    assert retried.tasks[0].submitted_answer is None
    archives = (await db.execute(select(StudyAssessmentResponseArchive))).scalars().all()
    assert len(archives) == 1

    # Revoking consent OR revoking grant denies further access with 404
    seeded["grant"].revoked_at = datetime.now(timezone.utc)
    db.add(seeded["grant"])
    await db.commit()
    with pytest.raises(HTTPException) as revoked_exc:
        await read_managed_linked_assignment(db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid)
    assert revoked_exc.value.status_code == 404


@pytest.mark.asyncio
async def test_expired_deadline_and_manifest_hash_pinning_fail_closed(db, org, admin_user, regular_user):
    seeded = await _seed_pilot_environment(db, org, admin_user, regular_user, due_date="2020-01-01")
    course = seeded["course"]
    release = seeded["release"]
    asg_act = seeded["asg_act"]
    task_num = seeded["task_num"]
    learner = _to_public_user(regular_user)

    with pytest.raises(HTTPException) as past_due_exc:
        await save_managed_assignment_task_draft(
            db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid,
            task_num.assignment_task_uuid, {"answer": "2.5"},
        )
    assert past_due_exc.value.status_code == 403

    # Build manifest pinning rejects unreviewed hashes or unversioned entry URLs (P1)
    tampered = _valid_lab_content()
    tampered["virtual_lab"]["build_sha256"] = "0" * 64
    with pytest.raises(HTTPException) as hash_exc:
        validate_governed_virtual_lab_content(tampered)
    assert hash_exc.value.status_code == 409

    unversioned = _valid_lab_content(entry_url="https://labs.studytomy.com/conservation-momentum/index.html")
    with pytest.raises(HTTPException) as path_exc:
        validate_governed_virtual_lab_content(unversioned)
    assert path_exc.value.status_code == 409


@pytest.mark.asyncio
async def test_postgres_concurrent_managed_submissions(postgres_session_factory):
    from uuid import uuid4
    from src.db.organizations import Organization
    from src.db.roles import Role
    from src.db.user_organizations import UserOrganization

    tag = uuid4().hex[:12]
    async with postgres_session_factory() as db:
        role = (await db.execute(select(Role).limit(1))).scalar_one_or_none()
        if role is None:
            role = Role(
                role_uuid=f"role_{tag}",
                name=f"Role {tag}",
                description="",
                rights={},
                role_type="TYPE_GLOBAL",
            )
            db.add(role)
            await db.flush()
        org = Organization(
            name=f"Concurrent Lab Org {tag}",
            org_uuid=f"org_pg_{tag}",
            slug=f"pg-lab-{tag}",
            email=f"pg-{tag}@example.invalid",
        )
        admin_user = User(
            username=f"pg_admin_{tag}",
            first_name="PG",
            last_name="Teacher",
            email=f"pg_admin_{tag}@example.invalid",
            password="hashedpassword",
            user_uuid=f"user_pg_admin_{tag}",
            creation_date="2026-01-01",
            update_date="2026-01-01",
        )
        regular_user = User(
            username=f"pg_learner_{tag}",
            first_name="PG",
            last_name="Learner",
            email=f"pg_learner_{tag}@example.invalid",
            password="hashedpassword",
            user_uuid=f"user_pg_learner_{tag}",
            creation_date="2026-01-01",
            update_date="2026-01-01",
        )
        db.add_all([org, admin_user, regular_user])
        await db.flush()
        db.add_all([
            UserOrganization(user_id=admin_user.id, org_id=org.id, role_id=role.id, creation_date="2026-01-01", update_date="2026-01-01"),
            UserOrganization(user_id=regular_user.id, org_id=org.id, role_id=role.id, creation_date="2026-01-01", update_date="2026-01-01"),
        ])
        await db.flush()
        seeded = await _seed_pilot_environment(db, org, admin_user, regular_user, tag=tag)
        course_uuid = seeded["course"].course_uuid
        release_uuid = seeded["release"].uuid
        asg_activity_uuid = seeded["asg_act"].activity_uuid
        task_num_uuid = seeded["task_num"].assignment_task_uuid
        task_file_uuid = seeded["task_file"].assignment_task_uuid
        assignment_id = seeded["assignment"].id
        learner = _to_public_user(regular_user)

        await save_managed_assignment_task_draft(
            db, org, learner, course_uuid, release_uuid, asg_activity_uuid,
            task_num_uuid, {"answer": "2.5"},
        )
        await save_managed_assignment_task_draft(
            db, org, learner, course_uuid, release_uuid, asg_activity_uuid,
            task_file_uuid, {"fileUUID": f"pg_submission_u{learner.id}.zip"},
        )
        await db.commit()

    async def worker(idem_key: str):
        async with postgres_session_factory() as wdb:
            worg = await wdb.get(Organization, org.id)
            res = await submit_managed_linked_assignment(
                wdb, worg, learner, course_uuid, release_uuid, asg_activity_uuid,
                idempotency_key=idem_key,
            )
            await wdb.commit()
            return res

    results = await asyncio.gather(
        worker(f"pg-conc-1-{tag}"),
        worker(f"pg-conc-2-{tag}"),
        return_exceptions=True,
    )
    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], HTTPException) and failures[0].status_code == 409

    async with postgres_session_factory() as verify_db:
        rows = (
            await verify_db.execute(
                select(AssignmentUserSubmission).where(
                    AssignmentUserSubmission.assignment_id == assignment_id,
                    AssignmentUserSubmission.user_id == learner.id,
                )
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].submission_status == AssignmentUserSubmissionStatus.SUBMITTED

