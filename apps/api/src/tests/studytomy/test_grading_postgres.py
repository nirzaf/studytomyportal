"""Verify managed grade finalization against the migrated PostgreSQL database."""

from asyncio import Barrier, Event, TaskGroup, sleep, timeout
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

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
from src.db.courses.certifications import Certifications, CertificateUser
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.access import StudyCourseAccessGrant
from src.db.studytomy.education import StudyCourseRelease
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import StudyBusinessEvent, StudyPolicyKey, StudyPolicyVersion
from src.db.trails import Trail
from src.db.users import PublicUser, User
from src.security.session_context import SessionProvenance, set_session_provenance
from src.services.courses.activities import assignments
from src.services.courses.certifications import (
    get_all_user_certificates,
    get_certificate_by_user_certification_uuid,
)
from src.services.studytomy import access_policy, assignment_adapter, awards, grading, releases
from src.services.studytomy.adaptive_reports import _snapshot as progress_report_snapshot
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.metrics import release_completion_observations
from src.services.studytomy.prerequisites import managed_assessed_pass_status
from src.tests.studytomy.test_content_evidence import _seed_postgres_release_sources
from src.tests.studytomy.test_studytomy_access import _synthetic_policy_body


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
async def test_issued_round_retains_exact_started_release(
    postgres_session_factory, monkeypatch, managed_round,
):
    org, user, course, assignment, task, attempt, actor = managed_round
    async with postgres_session_factory() as db:
        stored = await db.get(AssignmentUserSubmission, attempt.id)
        assignment = await db.get(Assignment, assignment.id)
        task = await db.get(AssignmentTask, task.id)
        assignment.published = True
        target = "activity_pass_gated"
        text_target = "activity_pass_gated_text"
        pdf = b"%PDF-1.4\n%%EOF\n"
        media_reference = f"courses/{course.course_uuid}/activities/{target}/documentpdf/practice.pdf"
        media_manifest = {media_reference: {
            "sha256": sha256(pdf).hexdigest(), "size_bytes": len(pdf),
            "media_type": "application/pdf", "storage_reference": "studytomy/releases/synthetic/practice.bin",
        }}
        snapshot = {
            "schema": "course_content_evidence_v4",
            "course": course.model_dump(mode="json"),
            "assignments": [assignment.model_dump(mode="json")],
            "assignment_tasks": [task.model_dump(mode="json")],
            "chapters": [{"id": assignment.chapter_id, "lock_type": "public"}],
            "chapter_order": [[assignment.chapter_id, 1]],
            "activity_order": [[assignment.chapter_id, 0, 2], [assignment.chapter_id, -1, 3]],
            "activities": [
                {"id": assignment.activity_id, "published": True, "activity_sub_type": "SUBTYPE_ASSIGNMENT_ANY"},
                {"id": 0, "activity_uuid": target, "published": True, "lock_type": "public",
                 "activity_type": "TYPE_DOCUMENT", "activity_sub_type": "SUBTYPE_DOCUMENT_PDF",
                 "content": {"filename": "practice.pdf"}},
                {"id": -1, "activity_uuid": text_target, "published": True, "lock_type": "public",
                 "name": "Synthetic follow-up", "activity_type": "TYPE_DYNAMIC",
                 "activity_sub_type": "SUBTYPE_DYNAMIC_PAGE", "content": {"type": "doc", "content": [
                     {"type": "paragraph", "content": [{"type": "text", "text": "Synthetic practice"}]},
                 ]}},
            ],
        }
        review, snapshot, policies = await _seed_postgres_release_sources(
            db, org, user.id, uuid4().hex, snapshot=snapshot, course_uuid=course.course_uuid,
        )
        policies[3].body = _synthetic_policy_body(StudyPolicyKey.D_16, {
            "certificate_presentation": {"managed_certificates_by_course": {course.course_uuid: {
                "award_name": "Synthetic certificate", "issuer_name": "Synthetic issuer",
                "verification": "private", "issuance": "reviewed",
                "changes": "reviewed_assessment_corrections",
                "required_assessment_uuids": [assignment.assignment_uuid],
            }}},
        }).model_dump(mode="json")
        release = StudyCourseRelease(
            org_id=org.id, course_uuid=course.course_uuid, review_id=review.id,
            snapshot_sha256=review.revision_sha256, snapshot=snapshot, media_manifest=media_manifest,
            assessment_policy_id=policies[0].id, progression_policy_id=policies[1].id,
            retention_policy_id=policies[2].id, rights_policy_id=policies[3].id,
            retention_category="synthetic", policy_binding={"versions": {"D-16": {
                "policy_id": policies[3].id, "body_sha256": canonical_request_hash(policies[3].body),
            }}},
        )
        profile = StudyLearnerProfile(org_id=org.id, user_id=user.id, created_by_user_id=user.id,
                                      control_mode="independent")
        db.add_all([release, profile])
        await db.flush()
        now = datetime.now(UTC)
        db.add(StudyCourseAccessGrant(
            org_id=org.id, learner_profile_id=profile.id, course_uuid=course.course_uuid,
            release_id=release.id, source_kind="free", source_uuid=f"synthetic_{uuid4()}",
            start_basis="fixed_period", starts_at=now - timedelta(minutes=1),
            ends_at=now + timedelta(minutes=1), created_at=now,
        ))
        await db.flush()
        authority = AsyncMock(return_value=SimpleNamespace(release=release))
        monkeypatch.setattr(access_policy, "resolve_managed_release_authority", authority)
        monkeypatch.setattr(grading, "_current_policies", AsyncMock(return_value=(policies[0],)))

        original = await grading.matching_grading_contract(db, assignment, stored, [task])
        assert "assessment_release_uuid" not in original.after_state
        assert not await grading.issue_native_grading_contract(db, assignment, actor)
        authority.assert_not_awaited()

        stored.attempt_number += 1
        await db.flush()
        assert await grading.issue_native_grading_contract(db, assignment, actor)
        bound = await grading.matching_grading_contract(db, assignment, stored, [task])
        assert bound.after_state["assessment_release_uuid"] == release.uuid
        assert bound.after_state["assessment_release_sha256"] == release.snapshot_sha256
        authority.assert_awaited_once()

        ambiguous = SimpleNamespace(
            uuid=release.uuid,
            snapshot_sha256=release.snapshot_sha256,
            assessment_policy_id=release.assessment_policy_id,
            snapshot=snapshot | {"activities": [*snapshot["activities"], snapshot["activities"][0]]},
        )
        authority.return_value = SimpleNamespace(release=ambiguous)
        assert await grading._assessed_release_binding(
            db, assignment, actor, grading.grading_contract_digest(assignment, [task]),
        ) == {}
        authority.return_value = SimpleNamespace(release=release)

        decisions = {
            "completion_prerequisites": {"assessed_pass_by_course": {course.course_uuid: [{
                "kind": "assessed_pass", "target_activity_uuid": item,
                "assessment_assignment_uuid": assignment.assignment_uuid,
            } for item in (target, text_target)]}},
        }
        policy_body = _synthetic_policy_body(StudyPolicyKey.D_09, decisions)
        progression = SimpleNamespace(id=release.progression_policy_id, body=policy_body.model_dump(mode="json"))

        async def status_for(user_id=user.id):
            return await managed_assessed_pass_status(db, policy_body, release, user_id, target)

        async def delivery_for():
            return await access_policy._assessed_pass_delivery(
                db, release, progression, user.id, target, lock_result=True,
            )

        assert await status_for() == "assessment_pass_required"
        assert await delivery_for() == (False, "assessment_pass_required")
        stored.submission_status = AssignmentUserSubmissionStatus.GRADED
        computed = {"grade": 90, "max_grade": 100, "passing_threshold": 50, "passed": True, "tasks": []}
        await grading.record_assessment_result(db, assignment, stored, bound, computed, actor)
        await db.flush()
        assert await status_for() == "assessment_pass_delivery_pending"
        assert await delivery_for() == (False, "assessment_pass_delivery_pending")
        assert await status_for(user.id + 1_000_000) == "assessment_pass_required"
        decisions["release_schedule"] = {
            "assessed_pass_delivery_activities": {course.course_uuid: [target, text_target]},
            "ungated_media_activities": {course.course_uuid: [target]},
            "ungated_text_activities": {course.course_uuid: [text_target]},
        }
        progression.body = _synthetic_policy_body(StudyPolicyKey.D_09, decisions).model_dump(mode="json")
        assert await delivery_for() == (True, "assessment_pass_delivery_pending")

        result = await grading.latest_assessment_result(db, assignment, stored)
        appeal = StudyBusinessEvent(
            org_id=org.id, actor_user_id=user.id, action="studytomy.assessment.appeal_requested",
            operation_uuid=result.resource_uuid, resource_uuid=result.event_uuid,
            resource_kind="assignment_result",
        )
        media_policy = SimpleNamespace(
            id=90015,
            body=_synthetic_policy_body(StudyPolicyKey.D_15, {
                "supported_formats": {"max_bytes": 1024, "media_types": ["application/pdf"]},
            }).model_dump(mode="json"),
        )
        monkeypatch.setattr(access_policy, "_authorized_release", AsyncMock(
            return_value=(release, progression, profile.id),
        ))
        monkeypatch.setattr(access_policy, "_current_policies", AsyncMock(return_value=(media_policy,)))
        lesson_args = (db, org, actor, course.course_uuid, release.uuid)
        lesson = await access_policy.read_managed_text_lesson(*lesson_args, text_target)
        assert lesson.paragraphs == ["Synthetic practice"]
        assert "completion" not in lesson.model_dump(mode="json")
        index = await access_policy.list_managed_text_lessons(*lesson_args)
        assert {lesson.activity_uuid for lesson in index.lessons if lesson.available} == {text_target, target}
        with pytest.raises(HTTPException) as no_completion_policy:
            await access_policy.acknowledge_managed_text_completion(*lesson_args, text_target)
        assert no_completion_policy.value.status_code == 404
        decisions["completion_prerequisites"]["course_completion_text_activities"] = {
            course.course_uuid: [text_target],
        }
        progression.body = _synthetic_policy_body(StudyPolicyKey.D_09, decisions).model_dump(mode="json")
        before = await access_policy.read_managed_text_lesson(*lesson_args, text_target)
        assert before.completion is not None and not before.completion.activity_completed
        completed = await access_policy.acknowledge_managed_text_completion(*lesson_args, text_target)
        assert completed.activity_completed and completed.course_completed
        assert await access_policy.acknowledge_managed_text_completion(*lesson_args, text_target) == completed
        reloaded = await access_policy.read_managed_text_lesson(*lesson_args, text_target)
        assert reloaded.completion == completed
        completion_events = (await db.execute(select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.action.in_((
                "studytomy.activity.completion_acknowledged", "studytomy.course.completion_recorded",
            )),
        ))).scalars().all()
        assert len(completion_events) == 2
        assert all(event.after_state == {"source_snapshot_digest": release.snapshot_sha256}
                   for event in completion_events)
        certificate_reviewer = PublicUser.model_validate(await db.get(User, review.owner_user_id))
        staff_access = AsyncMock()
        monkeypatch.setattr(awards, "authorize_studytomy_staff_action", staff_access)
        await awards.require_managed_certificate_workspace(db, org, certificate_reviewer, course.course_uuid)
        assert staff_access.await_args.kwargs["target_scope_kind"] == "course"
        with pytest.raises(HTTPException) as missing_course:
            await awards.require_managed_certificate_workspace(db, org, certificate_reviewer, f"course_{uuid4()}")
        assert missing_course.value.status_code == 404
        staff_access.reset_mock()
        monkeypatch.setattr(releases, "load_release_policy_binding", AsyncMock(return_value=SimpleNamespace(
            model_dump=lambda **_: release.policy_binding,
            versions={StudyPolicyKey.D_16: SimpleNamespace(policy_id=policies[3].id)},
        )))
        award_scope = (course.course_uuid, release.uuid, profile.learner_uuid)
        with pytest.raises(HTTPException) as own_preview:
            await awards.preview_managed_certificate_eligibility(db, org, actor, *award_scope)
        assert own_preview.value.status_code == 404
        staff_access.assert_not_awaited()
        eligible = await awards.preview_managed_certificate_eligibility(db, org, certificate_reviewer, *award_scope)
        assert eligible.eligible and eligible.completion_event_uuid
        assert eligible.assessment_result_uuids == {assignment.assignment_uuid: result.event_uuid}
        assert [call.kwargs["target_scope_kind"] for call in staff_access.await_args_list] == ["course", "learner"]
        assert eligible.issuance_enabled
        issue_args = (db, org, certificate_reviewer, *award_scope)
        with pytest.raises(HTTPException) as stale_award:
            await awards.issue_managed_certificate(
                *issue_args, awards.ManagedCertificateIssue(evidence_sha256="0" * 64),
            )
        assert stale_award.value.detail["code"] == "MANAGED_CERTIFICATE_EVIDENCE_CHANGED"
        command = awards.ManagedCertificateIssue(evidence_sha256=eligible.evidence_sha256)
        award = await awards.issue_managed_certificate(*issue_args, command)
        assert await awards.issue_managed_certificate(*issue_args, command) == award
        issued = await db.scalar(select(StudyBusinessEvent).where(StudyBusinessEvent.event_uuid == award.award_uuid))
        assert issued.subject_uuid == profile.learner_uuid
        assert issued.after_state == {
            "evidence_sha256": eligible.evidence_sha256,
            "completion_event_uuid": eligible.completion_event_uuid,
            "assessment_result_event_uuids": [result.event_uuid],
        }
        completion_time = next(event.created_at for event in completion_events
                               if event.action == "studytomy.course.completion_recorded")
        summary, report_sources = await progress_report_snapshot(
            db, org.id, profile, 0, course.id, course.course_uuid,
            completion_time - timedelta(seconds=1), completion_time + timedelta(seconds=1),
            0, completion_time + timedelta(seconds=1),
        )
        assert summary.native_completions == 0 and summary.reviewed_release_completions == 1
        assert report_sources["reviewed_release_completion"] == [{
            "event_uuid": eligible.completion_event_uuid,
            "release_uuid": release.uuid, "release_sha256": release.snapshot_sha256,
        }]
        assert "event_uuid" not in summary.model_dump_json()
        completion_scope = {"org_id": org.id, "course_uuid": course.course_uuid, "release_uuid": release.uuid}
        assert await release_completion_observations(
            db, **completion_scope, start=now, end=completion_time,
        ) == 0
        assert await release_completion_observations(
            db, **completion_scope, start=completion_time, end=completion_time + timedelta(seconds=1),
        ) == 1
        assert await release_completion_observations(
            db, **{**completion_scope, "release_uuid": f"course_release_{uuid4()}"},
            start=now, end=completion_time + timedelta(seconds=1),
        ) == 0
        late = now + timedelta(minutes=2)
        db.add(StudyBusinessEvent(
            org_id=org.id, actor_user_id=user.id, resource_kind="course_release",
            resource_uuid=release.uuid, action="studytomy.course.completion_recorded",
            after_state={"source_snapshot_digest": release.snapshot_sha256}, created_at=late,
            policy_refs=[release.progression_policy_id],
        ))
        await db.flush()
        assert await release_completion_observations(
            db, **completion_scope, start=late, end=late + timedelta(seconds=1),
        ) == 0
        assert await release_completion_observations(
            db, **completion_scope, start=completion_time, end=late + timedelta(seconds=1),
        ) == 1
        first_stage = BytesIO(pdf)
        monkeypatch.setattr(access_policy, "_stage_release_media", AsyncMock(return_value=first_stage))
        args = (db, org, actor, course.course_uuid, release.uuid, target)
        first = await access_policy.read_managed_activity_media(*args)
        with first.file:
            assert first.file.read() == pdf

        staged = BytesIO(pdf)

        async def open_appeal_while_staging(org_uuid, media):
            assert org_uuid == org.org_uuid
            assert media == media_manifest[media_reference]
            async with postgres_session_factory() as appeal_db:
                appeal_db.add(appeal)
                await appeal_db.commit()
            return staged

        monkeypatch.setattr(access_policy, "_stage_release_media", open_appeal_while_staging)
        with pytest.raises(HTTPException) as denied:
            await access_policy.read_managed_activity_media(*args)
        assert denied.value.status_code == 404
        assert staged.closed
        under_appeal = await awards.preview_managed_certificate_eligibility(db, org, certificate_reviewer, *award_scope)
        assert not under_appeal.eligible
        assert under_appeal.completion_event_uuid == eligible.completion_event_uuid
        assert under_appeal.missing_assessment_uuids == [assignment.assignment_uuid]
        with pytest.raises(HTTPException) as locked_lesson:
            await access_policy.read_managed_text_lesson(*lesson_args, text_target)
        assert locked_lesson.value.status_code == 404
        index = await access_policy.list_managed_text_lessons(*lesson_args)
        assert len(index.lessons) == 2
        assert all(not lesson.available and lesson.unavailable_reason == "assessment_pass_required"
                   for lesson in index.lessons)
        assert await status_for() == "assessment_pass_required"
        assert await delivery_for() == (False, "assessment_pass_required")
        for status, action, expected in (
            ("result_confirmed", "appeal_confirmed", "assessment_pass_delivery_pending"),
            ("reopened", "appeal_reopened", "assessment_pass_required"),
            ("result_confirmed", "appeal_confirmed", "assessment_pass_delivery_pending"),
        ):
            db.add(StudyBusinessEvent(
                org_id=org.id, action=f"studytomy.assessment.{action}",
                operation_uuid=result.resource_uuid, resource_uuid=appeal.event_uuid,
                resource_kind="assessment_appeal",
                after_state={"status": status, "result_uuid": result.event_uuid},
            ))
            await db.flush()
            assert await status_for() == expected
            assert (await delivery_for())[0] == (status == "result_confirmed")
        review_args = (*issue_args, UUID(award.award_uuid))
        current_rights = SimpleNamespace(id=policies[3].id + 1_000_000, body=policies[3].body)
        monkeypatch.setattr(awards, "_current_policies", AsyncMock(return_value=(policies[2], current_rights)))
        monkeypatch.setattr(releases, "load_release_policy_binding", AsyncMock(return_value=SimpleNamespace(
            model_dump=lambda **_: {"versions": {"D-16": {"policy_id": current_rights.id}}},
        )))
        with pytest.raises(HTTPException) as superseded_issue:
            await awards.issue_managed_certificate(*issue_args, command)
        assert superseded_issue.value.status_code == 404
        with pytest.raises(HTTPException) as unverified:
            await awards.review_managed_certificate_change(
                *review_args, awards.ManagedCertificateChange(corrected_result_uuid=UUID(result.event_uuid)),
            )
        assert unverified.value.detail["code"] == "MANAGED_CERTIFICATE_CORRECTION_UNVERIFIED"

        previous_result = result
        recorded_reviews = []
        for score, status, version in ((80, "corrected", 2), (40, "withdrawn", 3)):
            revision = StudyBusinessEvent(
                org_id=org.id, actor_user_id=certificate_reviewer.id,
                action=grading.RESULT_RECORDED_ACTION, reason=grading.APPEAL_CORRECTION_REASON,
                resource_kind="assignment_round", resource_uuid=result.resource_uuid,
                after_state={
                    **previous_result.after_state, "previous_event_uuid": previous_result.event_uuid,
                    "score_points": score, "passed": score >= 50, "version": version,
                },
            )
            db.add(revision)
            await db.flush()
            db.add(StudyBusinessEvent(
                org_id=org.id, subject_uuid=profile.learner_uuid,
                action="studytomy.assessment.appeal_corrected", resource_kind="assessment_appeal",
                resource_uuid=appeal.event_uuid, operation_uuid=result.resource_uuid,
                after_state={"status": "result_corrected", "result_uuid": revision.event_uuid},
            ))
            await db.flush()
            change_command = awards.ManagedCertificateChange(corrected_result_uuid=UUID(revision.event_uuid))
            change = await awards.review_managed_certificate_change(*review_args, change_command)
            assert (change.award_uuid, change.status, change.version) == (award.award_uuid, status, version)
            assert await awards.review_managed_certificate_change(*review_args, change_command) == change
            review_event = await db.scalar(select(StudyBusinessEvent).where(
                StudyBusinessEvent.event_uuid == change.review_uuid,
            ))
            assert review_event.policy_refs == sorted({policies[2].id, policies[3].id, current_rights.id})
            assert review_event.after_state["previous_event_uuid"] == (
                recorded_reviews[-1][1].review_uuid if recorded_reviews else award.award_uuid
            )
            assert review_event.after_state["assessment_result_event_uuids"] == [revision.event_uuid]
            recorded_reviews.append((change_command, change))
            previous_result = revision
        earlier_command, earlier_receipt = recorded_reviews[0]
        assert await awards.review_managed_certificate_change(*review_args, earlier_command) == earlier_receipt
        assert await status_for() == "assessment_pass_required"

        stored.attempt_number += 1
        stored.submission_status = AssignmentUserSubmissionStatus.PENDING
        task.max_grade_value += 1
        await db.flush()
        assert await grading.issue_native_grading_contract(db, assignment, actor)
        changed = await grading.matching_grading_contract(db, assignment, stored, [task])
        assert "assessment_release_uuid" not in changed.after_state
        grant = await db.scalar(select(StudyCourseAccessGrant).where(
            StudyCourseAccessGrant.release_id == release.id,
            StudyCourseAccessGrant.learner_profile_id == profile.id,
        ))
        grant.revoked_at = datetime.now(UTC)
        db.add(StudyLearnerProfile(
            org_id=org.id, user_id=certificate_reviewer.id, created_by_user_id=user.id,
            control_mode="independent",
        ))
        await db.flush()
        with pytest.raises(HTTPException) as no_session:
            await awards.verify_my_managed_certificate(db, org, actor, UUID(award.award_uuid))
        assert no_session.value.status_code == 403
        set_session_provenance(SessionProvenance(amr="password", org_id=org.id))
        try:
            verified = await awards.verify_my_managed_certificate(db, org, actor, UUID(award.award_uuid))
            assert verified.model_dump() == award.model_dump() | {
                "award_name": "Synthetic certificate", "issuer_name": "Synthetic issuer",
                "course_name": course.name, "status": "withdrawn", "version": 3,
            }
            listing = await awards.list_my_managed_certificates(db, org, actor)
            assert listing.awards == [verified] and listing.next_cursor is None
            assert not (await awards.list_my_managed_certificates(db, org, actor, UUID(award.award_uuid))).awards
            assert not (await awards.list_my_managed_certificates(db, org, certificate_reviewer)).awards
            with pytest.raises(HTTPException) as other_cursor:
                await awards.list_my_managed_certificates(db, org, certificate_reviewer, UUID(award.award_uuid))
            assert other_cursor.value.status_code == 404
            with pytest.raises(HTTPException) as other_profile:
                await awards.verify_my_managed_certificate(db, org, certificate_reviewer, UUID(award.award_uuid))
            assert other_profile.value.status_code == 404
        finally:
            set_session_provenance(None)


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
        contract_uuid = await db.scalar(select(StudyBusinessEvent.event_uuid).where(
            StudyBusinessEvent.org_id == org.id,
            StudyBusinessEvent.resource_uuid == result.resource_uuid,
            StudyBusinessEvent.action == grading.CONTRACT_FROZEN_ACTION,
        ))
        assert contract_uuid is not None
        assert result.after_state["grading_contract_event_uuid"] == contract_uuid
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
    ("correct_score", "historical", "certificate_changes"),
    [(False, False, False), (False, False, True), (True, False, False), (True, True, False)],
)
async def test_review_evidence_requires_direct_independent_authorized_reviewer(
    postgres_session_factory,
    managed_round,
    monkeypatch,
    mock_request,
    correct_score,
    historical,
    certificate_changes,
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
            workspace_path = f"/orgs/{org.org_uuid}/courses/{course.course_uuid}/award-workspace"
            workspace = await client.get(workspace_path, headers=direct)
            assert workspace.status_code == 200 and workspace.json() == {"available": True}
            assert "no-store" in workspace.headers["cache-control"]
            assert (await client.get(workspace_path, headers=headers("api_token"))).status_code == 403
            unassigned_workspace = await client.get(workspace_path, headers=headers("password", learner))
            assert unassigned_workspace.status_code == 403
            assert unassigned_workspace.json()["detail"]["code"] == "OWNER_ASSIGNMENT_REQUIRED"
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

            if not correct_score:
                text_uuid = f"activity_{uuid4()}"
                award_decisions = {
                    StudyPolicyKey.D_09: {
                        "release_schedule": {"ungated_text_activities": {course.course_uuid: [text_uuid]}},
                        "completion_prerequisites": {
                            "course_completion_text_activities": {course.course_uuid: [text_uuid]},
                        },
                    },
                    StudyPolicyKey.D_13: {"retention": {"course_release_retention_category": "synthetic"}},
                    StudyPolicyKey.D_16: {
                        "certificate_presentation": {"managed_certificates_by_course": {course.course_uuid: {
                            "award_name": "Synthetic reviewed award", "issuer_name": "Synthetic issuer",
                            "verification": "private", "issuance": "reviewed",
                            **({"changes": "reviewed_assessment_corrections"} if certificate_changes else {}),
                        }}},
                    },
                }
                for key, specified in award_decisions.items():
                    policy = await _seed_approved_policy(db, org, reviewer.id, key, specified=specified)
                    for responsibility in POLICY_REQUIRED_RESPONSIBILITIES[key]:
                        if responsibility not in owners:
                            owners[responsibility] = await _seed_active_owner(
                                db, org, reviewer.id, reviewer.id, responsibility,
                            )
                        db.add(StudyPolicyApproval(
                            org_id=org.id, policy_version_id=policy.id, responsibility=responsibility.value,
                            owner_assignment_id=owners[responsibility].id, approver_user_id=reviewer.id,
                            evidence_reference="synthetic-certificate-policy",
                            rationale="Synthetic certificate fixture", approved_at=datetime.now(UTC),
                        ))
                    await db.commit()
                retained = {
                    "course": {"id": course.id, "org_id": org.id, "course_uuid": course.course_uuid, "name": course.name},
                    "activities": [{"activity_uuid": text_uuid, "published": True,
                                    "activity_sub_type": "SUBTYPE_DYNAMIC_PAGE"}],
                }
                binding = await releases.load_release_policy_binding(
                    db, org, datetime.now(UTC), candidate_snapshot=retained,
                )
                policy_keys = (StudyPolicyKey.D_08, StudyPolicyKey.D_09, StudyPolicyKey.D_13, StudyPolicyKey.D_16)
                bound_policies = [await db.get(StudyPolicyVersion, binding.versions[key].policy_id) for key in policy_keys]
                review, snapshot, _ = await _seed_postgres_release_sources(
                    db, org, reviewer.id, uuid4().hex, snapshot=retained, course_uuid=course.course_uuid,
                    policy_versions=bound_policies,
                )
                certificate_release = StudyCourseRelease(
                    org_id=org.id, course_uuid=course.course_uuid, review_id=review.id,
                    snapshot_sha256=review.revision_sha256, snapshot=snapshot, media_manifest={},
                    assessment_policy_id=bound_policies[0].id, progression_policy_id=bound_policies[1].id,
                    retention_policy_id=bound_policies[2].id, rights_policy_id=bound_policies[3].id,
                    retention_category="synthetic", policy_binding=binding.model_dump(mode="json"),
                )
                db.add(certificate_release)
                unassigned_profile = StudyLearnerProfile(
                    org_id=org.id, user_id=review.owner_user_id, created_by_user_id=reviewer.id,
                )
                db.add(unassigned_profile)
                await db.flush()
                award_path = (
                    f"/orgs/{org.org_uuid}/courses/{course.course_uuid}/releases/{certificate_release.uuid}"
                    f"/learners/{profile.learner_uuid}"
                )
                preview = await client.get(f"{award_path}/award-eligibility", headers=direct)
                assert preview.status_code == 200, preview.text
                assert preview.json()["eligible"] is False
                unassigned_path = award_path.replace(profile.learner_uuid, unassigned_profile.learner_uuid)
                denied = await client.get(f"{unassigned_path}/award-eligibility", headers=direct)
                assert denied.status_code == 404
                assert denied.json()["detail"]["code"] == "RESOURCE_NOT_FOUND"
                await append_business_event(
                    db, org_id=org.id, actor_user_id=learner.id, actor_capacity="learner",
                    action="studytomy.course.completion_recorded", reason_code="RELEASE_REQUIREMENTS_COMPLETED",
                    resource_kind="course_release", resource_uuid=certificate_release.uuid,
                    policy_refs=[certificate_release.progression_policy_id],
                    after_state={"source_snapshot_digest": certificate_release.snapshot_sha256},
                    dedupe_key=f"studytomy:completion:{org.id}:{profile.id}:{certificate_release.uuid}",
                )
                await db.commit()
                preview = await client.get(f"{award_path}/award-eligibility", headers=direct)
                assert preview.status_code == 200 and preview.json()["eligible"] is True
                assert preview.json()["award_name"] == "Synthetic reviewed award"
                command = {"evidence_sha256": preview.json()["evidence_sha256"]}
                issuance = await client.post(f"{award_path}/awards", headers=direct, json=command)
                assert issuance.status_code == 200, issuance.text
                award_uuid = issuance.json()["award_uuid"]
                denied_change = await client.post(
                    f"{award_path}/awards/{award_uuid}/review",
                    headers=direct,
                    json={"corrected_result_uuid": str(uuid4())},
                )
                if certificate_changes:
                    assert denied_change.status_code == 409
                    assert denied_change.json()["detail"]["code"] == "MANAGED_CERTIFICATE_CORRECTION_UNVERIFIED"
                else:
                    assert denied_change.status_code == 404
                    assert denied_change.json()["detail"]["code"] == "MANAGED_CERTIFICATE_UNAVAILABLE"
                assert await db.scalar(select(StudyBusinessEvent.id).where(
                    StudyBusinessEvent.org_id == org.id,
                    StudyBusinessEvent.resource_kind == "managed_award",
                    StudyBusinessEvent.resource_uuid == award_uuid,
                )) is None
                replay = await client.post(f"{award_path}/awards", headers=direct, json=command)
                assert replay.status_code == 200 and replay.json() == issuance.json()

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

            previous_profile_status = profile.status
            profile.status = "active"
            profile.time_zone = "Australia/Sydney"
            await db.commit()
            visible = await assignments.read_assignment(
                mock_request,
                assignment.assignment_uuid,
                original_marker,
                db,
            )
            assert datetime.fromisoformat(visible.due_date) == datetime.fromisoformat(
                extension["expires_at"]
            )
            assert visible.deadline_time_zone == "Australia/Sydney"
            profile.time_zone = "Australia/Perth"
            await db.commit()
            refreshed = await assignments.read_assignment_from_activity_uuid(
                mock_request, visible.activity_uuid, original_marker, db,
            )
            assert refreshed.due_date == visible.due_date
            assert refreshed.deadline_time_zone == "Australia/Perth"
            profile.status = previous_profile_status
            profile.time_zone = None
            await db.commit()
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
                issued_at = datetime.now(UTC).isoformat()
                certificate = Certifications(
                    course_id=course.id, config={"title": "Synthetic retained award"},
                    certification_uuid=f"cert_{uuid4()}", creation_date=issued_at,
                    update_date=issued_at,
                )
                db.add(certificate)
                await db.flush()
                award = CertificateUser(
                    certification_id=certificate.id, user_id=learner.id,
                    user_certification_uuid=f"award_{uuid4()}", created_at=issued_at,
                    updated_at=issued_at,
                )
                db.add(award)
                await db.commit()
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
                async with postgres_session_factory() as verify_db:
                    retained_award = await verify_db.get(CertificateUser, award.id)
                    assert retained_award is not None
                    assert retained_award.user_certification_uuid == award.user_certification_uuid
                    retained_template = await verify_db.get(Certifications, certificate.id)
                    assert retained_template.config == {"title": "Synthetic retained award"}
                    own_awards = await get_all_user_certificates(
                        mock_request, PublicUser.model_validate(learner), verify_db, org_id=org.id
                    )
                    assert [
                        entry["certificate_user"].user_certification_uuid for entry in own_awards
                    ] == [award.user_certification_uuid]
                    outsider = PublicUser.model_validate(reviewer)
                    assert await get_all_user_certificates(
                        mock_request, outsider, verify_db, org_id=org.id
                    ) == []
                    with pytest.raises(HTTPException) as hidden:
                        await get_certificate_by_user_certification_uuid(
                            mock_request, award.user_certification_uuid, outsider, verify_db
                        )
                    assert hidden.value.status_code == 404
                    assert hidden.value.headers == {"Cache-Control": "private, no-store"}
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
                original = await db.scalar(select(StudyBusinessEvent).where(
                    StudyBusinessEvent.org_id == org.id,
                    StudyBusinessEvent.event_uuid == result["result_uuid"],
                ))
                assert revision.after_state["grading_contract_event_uuid"] == (
                    original.after_state["grading_contract_event_uuid"]
                )
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
