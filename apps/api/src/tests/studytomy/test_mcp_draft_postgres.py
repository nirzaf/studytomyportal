"""Native MCP draft writes against the migrated PostgreSQL schema."""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlmodel import select

from src.db.courses.activities import Activity
from src.db.courses.courses import Course
from src.db.organization_config import OrganizationConfig
from src.db.organizations import Organization
from src.db.roles import Role, RoleTypeEnum
from src.db.studytomy.governance import StudyBusinessEvent
from src.db.user_organizations import UserOrganization
from src.db.users import PublicUser, User
from src.services.ai.tools.base import ToolContext, make_synthetic_request
from src.services.mcp import draft_authoring
from src.services.mcp.draft_authoring import (
    DraftCourseInput,
    DraftLessonInput,
    create_draft_course,
    create_draft_lesson,
)


@pytest.mark.studytomy_postgres
async def test_native_mcp_drafts_are_private_scoped_and_retryable(postgres_session_factory, monkeypatch):
    code = uuid4().hex
    async with postgres_session_factory() as db:
        org = Organization(
            name="Synthetic MCP", slug=f"mcp-{code}", org_uuid=f"org_{code}",
            email=f"org-{code}@example.invalid",
        )
        other_org = Organization(
            name="Other synthetic MCP", slug=f"other-{code}", org_uuid=f"org_other_{code}",
            email=f"other-org-{code}@example.invalid",
        )
        users = [
            User(
                username=f"{label}_{code}", user_uuid=f"user_{label}_{code}",
                first_name="Synthetic", last_name="Author", email=f"{label}-{code}@example.invalid",
            )
            for label in ("creator", "other")
        ]
        db.add_all([org, other_org, *users])
        await db.flush()
        role = Role(
            id=10_000 + int(code[:7], 16), name="Synthetic teacher", org_id=org.id,
            role_uuid=f"role_{code}", role_type=RoleTypeEnum.TYPE_ORGANIZATION,
            rights={"courses": {"action_create": True, "action_read": True, "action_update": True}},
        )
        other_role = Role(
            id=role.id + 1, name=role.name, org_id=other_org.id,
            role_uuid=f"role_other_{code}", role_type=role.role_type, rights=role.rights,
        )
        db.add_all([role, other_role, OrganizationConfig(org_id=org.id, config={"config_version": "1.0"})])
        await db.flush()
        for user in users:
            db.add(UserOrganization(
                org_id=org.id, user_id=user.id, role_id=role.id, creation_date="", update_date="",
            ))
        db.add(UserOrganization(
            org_id=other_org.id, user_id=users[0].id, role_id=other_role.id,
            creation_date="", update_date="",
        ))
        await db.commit()

        ctx = ToolContext(
            request=make_synthetic_request(), db_session=db, user=PublicUser.model_validate(users[0]),
            org=org, org_slug=org.slug, surface="mcp", mode="execute", approved=True,
        )
        command = DraftCourseInput(request_id=str(uuid4()), title="Synthetic MCP draft")
        with pytest.raises(HTTPException) as unapproved:
            await create_draft_course(replace(ctx, approved=False), command)
        assert unapproved.value.status_code == 403

        async def fail_audit(*args, **kwargs):
            raise RuntimeError("synthetic audit write failure")

        async with postgres_session_factory() as failed_db:
            with monkeypatch.context() as patch:
                patch.setattr(draft_authoring, "append_business_event", fail_audit)
                with pytest.raises(RuntimeError, match="synthetic audit write failure"):
                    await create_draft_course(replace(ctx, db_session=failed_db), command)
            await failed_db.rollback()

        async def create_from_separate_session():
            async with postgres_session_factory() as concurrent_db:
                created = await create_draft_course(replace(ctx, db_session=concurrent_db), command)
                await concurrent_db.commit()
                return created

        created, concurrent = await asyncio.wait_for(
            asyncio.gather(create_from_separate_session(), create_from_separate_session()),
            timeout=15,
        )
        assert concurrent == created
        assert await create_draft_course(ctx, command) == created
        await db.commit()
        async with postgres_session_factory() as retry_db:
            with pytest.raises(HTTPException) as conflict:
                await create_draft_course(
                    replace(ctx, db_session=retry_db),
                    command.model_copy(update={"title": "Changed payload"}),
                )
            assert conflict.value.status_code == 409
        course = (await db.exec(select(Course).where(Course.org_id == org.id))).one()
        assert course.course_uuid == created["course_uuid"]
        assert course.studytomy_managed and not course.public and not course.published

        lesson_command = DraftLessonInput(
            request_id=str(uuid4()), title="Synthetic lesson", text="Plain lesson content",
            course_uuid=created["course_uuid"], chapter_uuid=created["chapter_uuid"],
        )
        with pytest.raises(HTTPException) as unauthorized:
            await create_draft_lesson(replace(ctx, user=PublicUser.model_validate(users[1])), lesson_command)
        assert unauthorized.value.status_code == 404

        with pytest.raises(HTTPException) as cross_tenant:
            await create_draft_lesson(replace(ctx, org=other_org, org_slug=other_org.slug), lesson_command)
        assert cross_tenant.value.status_code == 404

        lesson = await create_draft_lesson(ctx, lesson_command)
        await db.commit()
        assert await create_draft_lesson(ctx, lesson_command) == lesson
        await db.commit()
        activity = (await db.exec(select(Activity).where(Activity.course_id == course.id))).one()
        assert activity.activity_uuid == lesson["activity_uuid"]
        assert activity.org_id == org.id and not activity.published
        events = (await db.exec(select(StudyBusinessEvent).where(StudyBusinessEvent.org_id == org.id))).all()
        assert {event.action for event in events} == {
            "studytomy.mcp.create_draft_course", "studytomy.mcp.create_draft_lesson",
        }
        assert len(events) == 2
