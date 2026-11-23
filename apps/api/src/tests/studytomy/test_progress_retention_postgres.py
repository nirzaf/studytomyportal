import asyncio
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from sqlmodel import select

from src.db.courses.activities import Activity, ActivitySubTypeEnum, ActivityTypeEnum
from src.db.courses.certifications import CertificateUser, Certifications
from src.db.courses.chapter_activities import ChapterActivity
from src.db.courses.chapters import Chapter
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.roles import Role, RoleTypeEnum
from src.db.trail_runs import StatusEnum, TrailRun
from src.db.trail_steps import TrailStep
from src.db.trails import Trail
from src.db.user_organizations import UserOrganization
from src.db.users import APITokenUser, PublicUser, User
from src.services.admin.admin import (
    award_certificate,
    bulk_unenroll_users,
    reset_user_progress,
    uncomplete_activity,
    unenroll_user,
)
from src.services.courses.activities.activities import delete_activity
from src.services.courses.chapters import delete_chapter
from src.services.courses.courses import delete_course
from src.services.trail.trail import (
    remove_activity_from_trail,
    remove_course_from_trail,
)
from src.tests.conftest import USER_RIGHTS


@pytest.mark.studytomy_postgres
@pytest.mark.parametrize("operation", [
    "reset", "unenroll", "bulk_unenroll", "uncomplete", "leave", "remove_step",
    "delete_course", "delete_activity", "delete_chapter", "award_certificate", "delete_awarded_course",
])
async def test_native_mutation_waits_for_current_managed_course_status(postgres_session_factory, operation, monkeypatch):
    token = uuid4().hex
    async with postgres_session_factory() as db:
        org = Organization(
            name="Synthetic retention", slug=token, org_uuid=f"org_{token}",
            email=f"{token}@example.invalid",
        )
        user = User(username=token, user_uuid=f"user_{token}", email=org.email,
                    first_name="Synthetic", last_name="Learner")
        db.add_all([org, user])
        await db.flush()
        role = Role(name="Learner", org_id=org.id, role_uuid=f"role_{token}",
                    role_type=RoleTypeEnum.TYPE_ORGANIZATION, rights=USER_RIGHTS.model_dump())
        course = Course(org_id=org.id, course_uuid=f"course_{token}", name="Synthetic course",
                        public=False, published=False, open_to_contributors=False)
        trail = Trail(org_id=org.id, user_id=user.id)
        db.add_all([role, course, trail])
        await db.flush()
        db.add(UserOrganization(org_id=org.id, user_id=user.id, role_id=role.id,
                                creation_date="", update_date=""))
        activity = Activity(
            org_id=org.id, course_id=course.id, activity_uuid=f"activity_{token}",
            name="Synthetic lesson", activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE,
            creation_date="", update_date="",
        )
        run = TrailRun(trail_id=trail.id, course_id=course.id, org_id=org.id, user_id=user.id,
                       status=StatusEnum.STATUS_COMPLETED, creation_date="", update_date="")
        chapter = Chapter(org_id=org.id, course_id=course.id, name="Synthetic chapter")
        db.add_all([activity, chapter])
        if operation != "delete_awarded_course":
            db.add(run)
        await db.flush()
        db.add(ChapterActivity(
            org_id=org.id, course_id=course.id, chapter_id=chapter.id, activity_id=activity.id,
            order=1, creation_date="", update_date="",
        ))
        step = TrailStep(trailrun_id=run.id, activity_id=activity.id, course_id=course.id,
                         trail_id=trail.id, org_id=org.id, user_id=user.id, complete=True,
                         teacher_verified=False, grade="", creation_date="", update_date="")
        if operation != "delete_awarded_course":
            db.add(step)
        if operation in {"award_certificate", "delete_awarded_course"}:
            cert = Certifications(course_id=course.id, certification_uuid=f"cert_{token}")
            db.add(cert)
            if operation == "delete_awarded_course":
                await db.flush()
                award = CertificateUser(user_id=user.id, certification_id=cert.id, user_certification_uuid=f"award_{token}")
                db.add(award)
        await db.commit()

    actor = APITokenUser(org_id=org.id, rights={"courses": {"action_update": True}, "certifications": {"action_create": True}})
    async with postgres_session_factory() as publisher, postgres_session_factory() as resetter:
        cached = await resetter.get(Course, course.id)
        assert cached.studytomy_managed is False
        pending = await publisher.get(Course, course.id)
        pending.studytomy_managed = True
        await publisher.flush()
        request = Request({"type": "http"})
        learner = PublicUser.model_validate(user)

        async def allow_content_deletion(*args):
            return None

        match operation:
            case "reset":
                removal = reset_user_progress(actor, user.id, course.course_uuid, resetter)
            case "unenroll":
                removal = unenroll_user(actor, user.id, course.course_uuid, resetter)
            case "bulk_unenroll":
                removal = bulk_unenroll_users(actor, course.course_uuid, [user.id], resetter)
            case "uncomplete":
                removal = uncomplete_activity(actor, user.id, activity.activity_uuid, resetter)
            case "leave":
                removal = remove_course_from_trail(request, learner, course.course_uuid, resetter)
            case "remove_step":
                removal = remove_activity_from_trail(request, learner, activity.activity_uuid, resetter)
            case "delete_course" | "delete_awarded_course":
                monkeypatch.setattr("src.services.courses.courses.check_resource_access", allow_content_deletion)
                removal = delete_course(request, course.course_uuid, learner, resetter)
            case "delete_activity":
                monkeypatch.setattr("src.services.courses.activities.activities.check_resource_access", allow_content_deletion)
                removal = delete_activity(request, activity.activity_uuid, learner, resetter)
            case "delete_chapter":
                monkeypatch.setattr("src.services.courses.chapters.check_resource_access", allow_content_deletion)
                removal = delete_chapter(request, chapter.id, learner, resetter)
            case "award_certificate":
                removal = award_certificate(actor, user.id, course.course_uuid, request, resetter)
        pending_removal = asyncio.create_task(removal)
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(pending_removal), timeout=0.2)
            await publisher.commit()
            with pytest.raises(HTTPException) as blocked:
                await asyncio.wait_for(pending_removal, timeout=5)
            assert blocked.value.status_code == 409
            expected_code = (
                "MANAGED_AWARD_REVIEW_REQUIRED" if operation in {"award_certificate", "delete_awarded_course"}
                else "MANAGED_PROGRESS_REVIEW_REQUIRED"
            )
            assert blocked.value.detail["code"] == expected_code
        finally:
            if not pending_removal.done():
                pending_removal.cancel()
                await asyncio.gather(pending_removal, return_exceptions=True)
        await resetter.rollback()

    async with postgres_session_factory() as verifier:
        if operation == "delete_awarded_course":
            assert await verifier.get(CertificateUser, award.id) is not None
        else:
            assert (await verifier.get(TrailStep, step.id)).complete is True
            assert (await verifier.get(TrailRun, run.id)).status == StatusEnum.STATUS_COMPLETED
        assert await verifier.get(Course, course.id) is not None
        assert await verifier.get(Activity, activity.id) is not None
        assert await verifier.get(Chapter, chapter.id) is not None
        if operation == "award_certificate":
            assert (await verifier.execute(select(CertificateUser.id).where(CertificateUser.user_id == user.id))).first() is None
