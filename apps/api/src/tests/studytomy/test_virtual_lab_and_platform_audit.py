"""Regression tests for Virtual Science Lab Pilot & Platform Audit remediations (T-06..T-15)."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, UploadFile
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
from src.db.courses.drip_notifications import DripNotification
from src.db.studytomy.access import StudyCourseAccessGrant
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
from src.db.studytomy.privacy import StudyConsentEvent
from src.db.trail_runs import TrailRun
from src.db.trails import Trail
from src.db.user_organizations import UserOrganization
from src.db.usergroup_resources import UserGroupResource
from src.db.usergroup_user import UserGroupUser
from src.db.usergroups import UserGroup
from src.db.users import PublicUser, User
from src.security.file_validation import validate_upload
from src.services.courses.activities.uploads.sub_file import SUBMISSION_ALLOWED_TYPES, SUBMISSION_MAX_SIZE
from src.services.courses.drip_notify import run_drip_notifications
from src.services.playgrounds.playgrounds_generator import build_playground_system_prompt
from src.services.playgrounds.schemas.playgrounds_generator import (
    LearnerAdaptationContext,
    PlaygroundContext,
)
from src.services.studytomy.access_policy import (
    list_managed_text_lessons,
    read_managed_linked_assignment,
    read_managed_virtual_lab,
)
from src.services.studytomy.grounding import SourceProvenance, upload_draft_material
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.releases import (
    course_candidate_snapshot,
    validate_release_media_coverage,
)
from src.services.studytomy.virtual_labs import validate_governed_virtual_lab_content
from src.tests.studytomy.test_current_head_audit_remediations import _seed_policies_with_approvals


def _valid_lab_content(
    *,
    linked_assignment_activity_uuid: str | None = None,
    entry_url: str = "https://labs.studytomy.com/conservation-momentum/v1/index.html",
) -> dict:
    return {
        "virtual_lab": {
            "schema_version": "studytomy_virtual_lab_v1",
            "lab_id": "conservation-momentum",
            "experiment_id": "conservation-momentum",
            "build_version": "1.0.0-pilot.1",
            "build_sha256": "43c4a9668f9a9ed2a56954286a2141bae452f9c48af3a631b2b24e4db1e64181",
            "entry_url": entry_url,
            "units_system": "SI",
            "source_repository": "https://github.com/danielw-sudo/agentuto",
            "source_commit": "697025a14aabbd391072fbedc522d1d68bebcbc4",
            "linked_assignment_activity_uuid": linked_assignment_activity_uuid,
            "submission_formats_note": "Submit numerical answers and a PDF or ZIP report containing exported single-condition CSV files.",
        }
    }


def _to_public_user(user: User) -> PublicUser:
    return PublicUser(
        id=user.id,
        username=user.username,
        email=user.email,
        first_name=user.first_name or "",
        last_name=user.last_name or "",
        avatar_image=user.avatar_image or "",
        bio=user.bio or "",
        user_uuid=user.user_uuid,
        is_superadmin=user.is_superadmin,
    )


async def _seed_managed_lab_release(db, org, admin_user, regular_user):
    now = datetime.now(timezone.utc)
    course = Course(
        org_id=org.id,
        course_uuid="course_vl_pilot_1",
        name="Grade 12 Physics — Momentum Pilot",
        description="Sri Lankan A/L Physics Mechanics Pilot",
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
        chapter_uuid="chap_vl_1",
        name="Mechanics: Conservation of Momentum",
        description="",
        thumbnail_image="",
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(chapter)
    await db.flush()
    db.add(CourseChapter(org_id=org.id, course_id=course.id, chapter_id=chapter.id, order=1, creation_date="2026-01-01", update_date="2026-01-01"))

    asg_activity = Activity(
        org_id=org.id,
        course_id=course.id,
        activity_uuid="act_vl_asg_1",
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
    db.add(asg_activity)
    await db.flush()

    lab_activity = Activity(
        org_id=org.id,
        course_id=course.id,
        activity_uuid="act_vl_lab_1",
        name="Virtual Lab: Conservation of Linear Momentum",
        activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
        activity_sub_type="SUBTYPE_DYNAMIC_EMBED",
        content=_valid_lab_content(linked_assignment_activity_uuid=asg_activity.activity_uuid),
        details={},
        published=True,
        current_version=1,
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(lab_activity)
    await db.flush()

    db.add(ChapterActivity(org_id=org.id, course_id=course.id, chapter_id=chapter.id, activity_id=lab_activity.id, order=1, creation_date="2026-01-01", update_date="2026-01-01"))
    db.add(ChapterActivity(org_id=org.id, course_id=course.id, chapter_id=chapter.id, activity_id=asg_activity.id, order=2, creation_date="2026-01-01", update_date="2026-01-01"))

    assignment = Assignment(
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=asg_activity.id,
        assignment_uuid="asg_vl_1",
        title="Momentum Practical Report",
        description="Submit calculations and your PDF/ZIP practical report.",
        due_date="2026-12-31",
        published=True,
        grading_type=GradingTypeEnum.NUMERIC,
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(assignment)
    await db.flush()

    task_file = AssignmentTask(
        org_id=org.id,
        course_id=course.id,
        chapter_id=chapter.id,
        activity_id=asg_activity.id,
        assignment_id=assignment.id,
        assignment_task_uuid="task_vl_file_1",
        title="Upload Practical Report (PDF or ZIP with CSV)",
        description="Include Trial 1 (elastic) and Trial 2 (inelastic) readings.",
        hint="",
        assignment_type=AssignmentTaskTypeEnum.FILE_SUBMISSION,
        contents={"secret_answer_key": "DO_NOT_LEAK"},
        max_grade_value=10,
        order=0,
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
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
                    "ungated_lab_activities": {course.course_uuid: [lab_activity.activity_uuid]},
                },
                "completion_prerequisites": {"required": False},
                "mastery_gate": {"separate": True},
            },
            StudyPolicyKey.D_11: {"community_audiences": {"restricted": True}, "adult_contact_boundaries": {"moderated": True}, "moderation": {"enabled": True}},
            StudyPolicyKey.D_12: {"information_uses": {"learning_terms": ["course_release"]}, "consent": {"notice_versions": {"learning_terms": "v1"}, "notice_texts": {"learning_terms": "Approved"}}, "parent_report_scope": ["consent_management"]},
            StudyPolicyKey.D_13: {"retention": {"learner_evidence_retention_category": "education_7y"}, "record_access": {"assigned_reviewer_only": True}},
            StudyPolicyKey.D_15: {"accessibility_standard": {"wcag": "2.1-AA"}, "supported_formats": {"media_types": ["application/pdf"], "max_bytes": 10_000_000}, "service_recovery": {"fallback": "draft"}},
            StudyPolicyKey.D_16: {"content_rights": {"verified": True}, "review_approvals": {"independent_reviewer": True}, "certificate_presentation": {"none": True}},
            StudyPolicyKey.D_18: {"customer_terms": {"version": "v1"}, "selected_market_requirements": {"jurisdiction": "LK"}},
        },
    )
    edu_owner = owners[StudyOwnerFunction.EDUCATION]

    snapshot = await course_candidate_snapshot(db, course)
    validate_release_media_coverage(snapshot)
    sha = canonical_request_hash(snapshot)
    review = StudyCourseReview(
        org_id=org.id,
        course_uuid=course.course_uuid,
        revision_sha256=sha,
        owner_user_id=regular_user.id,
        reviewer_user_id=admin_user.id,
        reviewer_assignment_id=edu_owner.id,
        rights_evidence_id=1,
        accessibility_evidence_id=2,
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
        learner_uuid="lrn_vl_1",
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
        org_id=org.id,
        learner_profile_id=profile.id,
        eligibility_kind="learning",
        outcome="eligible",
        evidence_reference="ELIG-VL-1",
        policy_version_ids=el_policy_ids,
        reviewer_user_id=admin_user.id,
        owner_assignment_id=edu_owner.id,
        effective_from=now - timedelta(hours=1),
    ))
    db.add(StudyEligibilityDecision(
        org_id=org.id,
        learner_profile_id=profile.id,
        eligibility_kind="independent_participation",
        outcome="eligible",
        evidence_reference="ELIG-INDEP-VL-1",
        policy_version_ids=indep_policy_ids,
        reviewer_user_id=admin_user.id,
        owner_assignment_id=edu_owner.id,
        effective_from=now - timedelta(hours=1),
    ))
    db.add(StudyConsentEvent(
        org_id=org.id,
        learner_profile_id=profile.id,
        actor_user_id=regular_user.id,
        subject_user_id=regular_user.id,
        purpose_code="learning_terms",
        choice="accept",
        notice_version="v1",
        scope_codes=["course_release"],
        policy_version_ids=c_policy_ids,
        effective_at=now - timedelta(hours=1),
    ))
    db.add(StudyCourseAccessGrant(
        org_id=org.id,
        learner_profile_id=profile.id,
        course_uuid=course.course_uuid,
        release_id=release.id,
        source_kind="order_line",
        source_uuid="ord_vl_1",
        start_basis="fixed_period",
        starts_at=now - timedelta(hours=1),
        ends_at=now + timedelta(days=30),
        created_at=now - timedelta(hours=1),
    ))
    await db.commit()
    return course, release, lab_activity, asg_activity, assignment, task_file, profile


@pytest.mark.asyncio
async def test_t06_t08_governed_virtual_lab_and_linked_assignment_journey(db, org, admin_user, regular_user):
    course, release, lab_act, asg_act, assignment, task_file, _ = await _seed_managed_lab_release(
        db, org, admin_user, regular_user
    )
    other_user = User(
        username="stranger_vl",
        first_name="Stranger",
        last_name="Learner",
        email="stranger_vl@test.com",
        password="hashedpassword",
        user_uuid="user_stranger_vl",
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(other_user)
    await db.commit()
    await db.refresh(other_user)
    learner = _to_public_user(regular_user)
    stranger = _to_public_user(other_user)

    # 1. Arbitrary Embed without governed descriptor is still rejected (T-06)
    with pytest.raises(HTTPException) as exc:
        validate_governed_virtual_lab_content({"embed_url": "https://evil.example/widget"})
    assert exc.value.status_code == 409

    # 2. Unapproved origin is rejected (T-06)
    with pytest.raises(HTTPException) as exc_origin:
        validate_governed_virtual_lab_content(
            _valid_lab_content(entry_url="https://unapproved.example/lab/index.html")
        )
    assert exc_origin.value.status_code == 409

    # 3. Authorized learner sees the virtual lab in managed lesson index (and not raw assignment)
    index = await list_managed_text_lessons(db, org, learner, course.course_uuid, release.uuid)
    assert len(index.lessons) == 1
    assert index.lessons[0].format == "virtual_lab"
    assert index.lessons[0].available is True
    assert index.lessons[0].linked_assignment_activity_uuid == asg_act.activity_uuid

    # 4. Authorized learner reads pinned virtual lab descriptor
    lab = await read_managed_virtual_lab(db, org, learner, course.course_uuid, release.uuid, lab_act.activity_uuid)
    assert lab.lab_id == "conservation-momentum"
    assert lab.build_version == "1.0.0-pilot.1"
    assert lab.units_system == "SI"
    assert lab.linked_assignment is not None
    assert lab.linked_assignment.assignment_uuid == assignment.assignment_uuid

    # 5. Unauthorized user (learner B) is denied both lab and linked assignment (404)
    with pytest.raises(HTTPException) as exc_b:
        await read_managed_virtual_lab(db, org, stranger, course.course_uuid, release.uuid, lab_act.activity_uuid)
    assert exc_b.value.status_code == 404
    with pytest.raises(HTTPException) as exc_b_asg:
        await read_managed_linked_assignment(db, org, stranger, course.course_uuid, release.uuid, asg_act.activity_uuid)
    assert exc_b_asg.value.status_code == 404

    # 6. Seed learner A's graded submission and verify feedback is visible ONLY to learner A without leaking answer keys
    db.add(AssignmentUserSubmission(
        assignmentusersubmission_uuid="aus_vl_1",
        assignment_id=assignment.id,
        user_id=regular_user.id,
        submission_status=AssignmentUserSubmissionStatus.GRADED,
        grade=9,
        overall_feedback="Clear SI momentum conservation analysis.",
        attempt_number=1,
        creation_date="2026-01-02",
        update_date="2026-01-02",
    ))
    db.add(AssignmentTaskSubmission(
        assignment_task_submission_uuid="tsub_vl_1",
        task_submission={"fileUUID": "file_report.zip"},
        grade=9,
        task_submission_grade_feedback="Good elastic/inelastic comparison.",
        manually_graded=True,
        assignment_type=AssignmentTaskTypeEnum.FILE_SUBMISSION,
        user_id=regular_user.id,
        activity_id=asg_act.id,
        course_id=course.id,
        chapter_id=assignment.chapter_id,
        assignment_task_id=task_file.id,
        creation_date="2026-01-02",
        update_date="2026-01-02",
    ))
    await db.commit()

    asg_view = await read_managed_linked_assignment(
        db, org, learner, course.course_uuid, release.uuid, asg_act.activity_uuid
    )
    assert asg_view.submission_status == "GRADED"
    assert asg_view.grade == 9
    assert asg_view.overall_feedback == "Clear SI momentum conservation analysis."
    assert len(asg_view.tasks) == 1
    assert asg_view.tasks[0].student_grade == 9
    assert asg_view.tasks[0].teacher_feedback == "Good elastic/inelastic comparison."
    assert "DO_NOT_LEAK" not in asg_view.model_dump_json()
    assert "PDF" in asg_view.accepted_file_formats and "ZIP" in asg_view.accepted_file_formats


def test_t09_submission_validator_rejects_raw_csv_and_accepts_pdf_and_zip():
    csv_upload = UploadFile(
        file=io.BytesIO(b"t,total_p_x\n0,4.0\n"),
        filename="momentum.csv",
        headers=Headers({"content-type": "text/csv"}),
    )
    with pytest.raises(HTTPException) as exc:
        validate_upload(csv_upload, SUBMISSION_ALLOWED_TYPES, SUBMISSION_MAX_SIZE)
    assert exc.value.status_code == 415

    pdf_upload = UploadFile(
        file=io.BytesIO(b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"),
        filename="report.pdf",
        headers=Headers({"content-type": "application/pdf"}),
    )
    media_type, _ = validate_upload(pdf_upload, SUBMISSION_ALLOWED_TYPES, SUBMISSION_MAX_SIZE)
    assert media_type == "application/pdf"

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("run-1.csv", "t,total_p_x\n0,4.0\n")
    zip_buf.seek(0)
    zip_upload = UploadFile(
        file=zip_buf,
        filename="momentum-report.zip",
        headers=Headers({"content-type": "application/zip"}),
    )
    zip_media, _ = validate_upload(zip_upload, SUBMISSION_ALLOWED_TYPES, SUBMISSION_MAX_SIZE)
    assert zip_media == "application/zip"


@pytest.mark.asyncio
async def test_t11_grounding_provenance_persisted_and_duplicate_conflict_rejected(db, org, admin_user, monkeypatch):
    monkeypatch.setenv("LEARNHOUSE_STUDYTOMY_GROUNDED_DRAFTS_ENABLED", "true")
    course = Course(
        org_id=org.id,
        course_uuid="course_prov_1",
        name="Physics Provenance",
        description="",
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
    lesson = Activity(
        org_id=org.id,
        course_id=course.id,
        activity_uuid="act_prov_1",
        name="Momentum Theory",
        activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
        activity_sub_type="SUBTYPE_DYNAMIC_PAGE",
        content={},
        details={},
        published=False,
        current_version=1,
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(lesson)
    await db.commit()

    fake_pdf = b"%PDF-1.4 fake"
    fake_pages = [{"page": 1, "text": "Conservation of linear momentum states total momentum is constant."}]
    prov_a = SourceProvenance(
        parent_book_title="G.C.E. A/L Physics Resource Book",
        publisher_authority="NIE Sri Lanka",
        syllabus_edition="2017 Revised",
        resource_type="resource_book",
        source_language="en",
        printed_page_range="45-52",
        rights_status="unverified",
    )
    prov_b = SourceProvenance(
        parent_book_title="Different Title",
        publisher_authority="NIE Sri Lanka",
        rights_status="verified_permitted",
    )

    actor = _to_public_user(admin_user)
    with patch("src.services.studytomy.grounding.authorize_draft_material_lesson", new=AsyncMock(return_value=(course, lesson))), \
         patch("src.services.studytomy.grounding._extract_pdf", return_value=(fake_pdf, fake_pages)):
        upload1 = UploadFile(file=io.BytesIO(fake_pdf), filename="unit2.pdf")
        receipt1 = await upload_draft_material(
            db, None, actor, org.org_uuid, course.course_uuid, lesson.activity_uuid, upload1,
            source_provenance=prov_a,
        )
        assert receipt1.source_provenance is not None
        assert receipt1.source_provenance.publisher_authority == "NIE Sri Lanka"

        # Same bytes + identical provenance is idempotent
        upload2 = UploadFile(file=io.BytesIO(fake_pdf), filename="unit2.pdf")
        receipt2 = await upload_draft_material(
            db, None, actor, org.org_uuid, course.course_uuid, lesson.activity_uuid, upload2,
            source_provenance=prov_a,
        )
        assert receipt2.source_uuid == receipt1.source_uuid

        # Same bytes + conflicting provenance raises 409 instead of silently dropping or overwriting
        upload3 = UploadFile(file=io.BytesIO(fake_pdf), filename="unit2.pdf")
        with pytest.raises(HTTPException) as exc:
            await upload_draft_material(
                db, None, actor, org.org_uuid, course.course_uuid, lesson.activity_uuid, upload3,
                source_provenance=prov_b,
            )
        assert exc.value.status_code == 409


def test_t12_playground_prompt_aligns_with_script_free_renderer():
    enabled_prompt = build_playground_system_prompt(
        PlaygroundContext(
            playground_name="Momentum Concepts",
            playground_description="Explore momentum",
            learner_adaptation=LearnerAdaptationContext(
                enabled=True,
                level_labels=["Foundation", "Extension"],
                focus_skills=["Momentum calculation"],
            ),
        )
    )
    assert "script-free" in enabled_prompt.lower()
    assert "script-src 'none'" in enabled_prompt
    assert "do not rely on javascript or <canvas>" in enabled_prompt.lower()
    assert "Foundation" in enabled_prompt and "Extension" in enabled_prompt
    assert "Momentum calculation" in enabled_prompt

    disabled_prompt = build_playground_system_prompt(
        PlaygroundContext(
            playground_name="Momentum Concepts",
            playground_description="Explore momentum",
            learner_adaptation=LearnerAdaptationContext(enabled=False),
        )
    )
    assert "LEARNER-LEVEL AND FOCUS-SKILL ADAPTATION" not in disabled_prompt
    assert "script-free" in disabled_prompt.lower()


@pytest.mark.asyncio
async def test_t15_drip_notifications_reauthorize_membership_usergroup_and_protected_learners(db, org, course, admin_user, regular_user):
    other_user = User(
        username="protected_drip_learner",
        first_name="Protected",
        last_name="Learner",
        email="protected_drip@test.com",
        password="hashedpassword",
        user_uuid="user_protected_drip",
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(other_user)
    await db.flush()
    db.add(UserOrganization(user_id=other_user.id, org_id=org.id, role_id=1, creation_date="2026-01-01", update_date="2026-01-01"))
    now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    course.published = True
    course.studytomy_managed = False
    course.drip_settings = {"enabled": True, "anchor": "enrollment", "notify_email": True}
    db.add(course)
    ch = Chapter(
        org_id=org.id,
        course_id=course.id,
        chapter_uuid="chap_t15_1",
        name="Chapter Delayed",
        description="",
        thumbnail_image="",
        drip_rule={"mode": "delay", "delay_days": 5},
        creation_date="2026-01-01",
        update_date="2026-01-01",
    )
    db.add(ch)
    await db.flush()
    db.add(CourseChapter(org_id=org.id, course_id=course.id, chapter_id=ch.id, order=1, creation_date="2026-01-01", update_date="2026-01-01"))

    # Enroll regular_user (who we will remove from org) and other_user (who has a protected StudyLearnerProfile)
    for idx, u in enumerate((regular_user, other_user), start=1):
        tr = Trail(org_id=org.id, user_id=u.id, trail_uuid=f"tr_t15_{idx}", creation_date="2026-01-01", update_date="2026-01-01")
        db.add(tr)
        await db.flush()
        db.add(TrailRun(
            trail_id=tr.id,
            course_id=course.id,
            org_id=org.id,
            user_id=u.id,
            creation_date=(now - timedelta(days=5, hours=2)).isoformat(),
            update_date=(now - timedelta(days=5, hours=2)).isoformat(),
        ))

    # 1. Remove regular_user from org while retaining TrailRun
    existing_uos = (
        await db.execute(
            select(UserOrganization).where(
                UserOrganization.org_id == org.id,
                UserOrganization.user_id == regular_user.id,
            )
        )
    ).scalars().all()
    for row in existing_uos:
        await db.delete(row)

    # 2. Mark other_user as a protected Studytomy learner profile
    db.add(StudyLearnerProfile(
        org_id=org.id,
        user_id=other_user.id,
        learner_uuid="lrn_t15_protected",
        created_by_user_id=admin_user.id,
        status=StudyLearnerProfileStatus.ACTIVE.value,
        control_mode=StudyLearnerControlMode.DEPENDENT.value,
    ))
    await db.commit()

    with patch("src.services.users.emails.send_drip_unlocked_email") as send, \
         patch("src.services.nudges.links.org_base_url", new=AsyncMock(return_value="https://lms.studytomy.com")):
        stats = await run_drip_notifications(db, now)
        assert stats.emails == 0
        assert not send.called

    # Verify zero DripNotification claims were created for unauthorized/protected users
    claims = (await db.execute(select(DripNotification).where(DripNotification.course_id == course.id))).scalars().all()
    assert claims == []

    # 3. Restore org membership for regular_user, but gate course behind a UserGroup they are NOT in
    db.add(UserOrganization(user_id=regular_user.id, org_id=org.id, role_id=1, creation_date="2026-01-01", update_date="2026-01-01"))
    ug = UserGroup(name="Cohort A", description="", org_id=org.id, usergroup_uuid="ug_t15_a", creation_date="2026-01-01", update_date="2026-01-01")
    db.add(ug)
    await db.flush()
    db.add(UserGroupResource(usergroup_id=ug.id, org_id=org.id, resource_uuid=course.course_uuid, creation_date="2026-01-01", update_date="2026-01-01"))
    await db.commit()

    with patch("src.services.users.emails.send_drip_unlocked_email") as send, \
         patch("src.services.nudges.links.org_base_url", new=AsyncMock(return_value="https://lms.studytomy.com")):
        stats_ug = await run_drip_notifications(db, now)
        assert stats_ug.emails == 0
        assert not send.called

    # 4. Add regular_user to UserGroup -> now authorized and receives exactly one email
    db.add(UserGroupUser(usergroup_id=ug.id, user_id=regular_user.id, org_id=org.id, creation_date="2026-01-01", update_date="2026-01-01"))
    await db.commit()

    with patch("src.services.users.emails.send_drip_unlocked_email") as send, \
         patch("src.services.nudges.links.org_base_url", new=AsyncMock(return_value="https://lms.studytomy.com")):
        stats_ok = await run_drip_notifications(db, now)
        assert stats_ok.emails == 1
        assert send.call_count == 1
        assert send.call_args.kwargs["email"] == regular_user.email
