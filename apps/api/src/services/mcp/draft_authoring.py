"""Bounded draft-only inputs for the Studytomy MCP authoring tools."""

from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator
from sqlmodel import select

from src.db.courses.activities import ActivityCreate, ActivitySubTypeEnum, ActivityTypeEnum
from src.db.courses.chapters import Chapter, ChapterCreate
from src.db.courses.course_chapters import CourseChapter
from src.db.courses.courses import Course, CourseCreate
from src.db.resource_authors import ResourceAuthor, ResourceAuthorshipEnum, ResourceAuthorshipStatusEnum
from src.db.users import PublicUser
from src.security.org_auth import is_org_member, require_org_create_permission
from src.security.rbac import AccessAction, check_resource_access
from src.services.ai.tools.base import ActionTier, ToolContext, ToolSpec
from src.services.courses.activities.activities import create_activity
from src.services.courses.chapters import create_chapter
from src.services.courses.courses import create_course
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.idempotency import claim_idempotency, complete_idempotency
from src.services.studytomy.policies import lock_organization

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_RequestKey = Annotated[str, StringConstraints(pattern=r"^" + _UUID + r"$")]
_CourseRef = Annotated[str, StringConstraints(pattern=r"^course_" + _UUID + r"$")]
_ChapterRef = Annotated[str, StringConstraints(pattern=r"^chapter_" + _UUID + r"$")]
_Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class DraftCourseInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_id: _RequestKey
    title: _Title

    @field_validator("title")
    @classmethod
    def title_has_no_control_characters(cls, value: str) -> str:
        if not value.isprintable():
            raise ValueError("The title must be a single line of printable text.")
        return value


class DraftLessonInput(DraftCourseInput):
    course_uuid: _CourseRef
    chapter_uuid: _ChapterRef
    text: Annotated[str, StringConstraints(max_length=12000)] = ""

    @field_validator("text")
    @classmethod
    def text_has_no_control_characters(cls, value: str) -> str:
        if any(not char.isprintable() and char not in "\n\r\t" for char in value):
            raise ValueError("The lesson text contains unsupported control characters.")
        if len(value.splitlines()) > 100:
            raise ValueError("The lesson text must have at most 100 lines.")
        return value


def lesson_text_document(text: str) -> dict:
    """Represent user input as text nodes, without interpreting HTML, URLs or Markdown."""
    return {
        "type": "doc",
        "content": [
            {"type": "paragraph", **({"content": [{"type": "text", "text": line}]} if line else {})}
            for line in (text.splitlines() or [""])
        ],
    }


async def _draft_actor(ctx: ToolContext) -> int:
    if ctx.surface != "mcp" or ctx.read_only or not ctx.approved or not isinstance(ctx.user, PublicUser):
        raise HTTPException(403, "Draft authoring requires approved MCP write access.")
    await lock_organization(ctx.db_session, ctx.org.id)
    if not await is_org_member(ctx.user.id, ctx.org.id, ctx.db_session):
        raise HTTPException(403, "Draft authoring requires organization membership.")
    await require_org_create_permission(ctx.user, ctx.org.id, ctx.db_session, "courses")
    return ctx.user.id


async def _claim(ctx: ToolContext, actor: int, operation: str, command: DraftCourseInput):
    return await claim_idempotency(
        ctx.db_session, org_id=ctx.org.id, actor_user_id=actor, operation=operation,
        idempotency_key=command.request_id, request_payload=command,
    )


async def _retain(
    ctx: ToolContext, actor: int, operation: str, command: DraftCourseInput,
    kind: str, reference: str, projection: dict,
) -> dict:
    await append_business_event(
        ctx.db_session, org_id=ctx.org.id, actor_user_id=actor,
        actor_capacity="mcp_course_author", action=f"studytomy.mcp.{operation}",
        reason_code="PRIVATE_DRAFT_CREATED", resource_kind=kind, resource_uuid=reference,
    )
    await complete_idempotency(
        ctx.db_session, org_id=ctx.org.id, actor_user_id=actor, operation=operation,
        idempotency_key=command.request_id, resource_kind=kind, resource_uuid=reference,
        response_projection=projection,
    )
    return projection


async def create_draft_course(ctx: ToolContext, command: DraftCourseInput) -> dict:
    actor = await _draft_actor(ctx)
    operation = "create_draft_course"
    replay = await _claim(ctx, actor, operation, command)
    if replay is not None:
        return replay.response_projection
    course = await create_course(
        ctx.request, ctx.org.id,
        CourseCreate(
            org_id=ctx.org.id, name=command.title, public=False, published=False,
            open_to_contributors=False,
        ),
        ctx.user, ctx.db_session, transactional_draft=True,
    )
    chapter = await create_chapter(
        ctx.request, ChapterCreate(org_id=ctx.org.id, course_id=course.id, name="Lessons"),
        ctx.user, ctx.db_session, transactional_draft=True,
    )
    return await _retain(ctx, actor, operation, command, "course", course.course_uuid, {
        "course_uuid": course.course_uuid, "chapter_uuid": chapter.chapter_uuid, "status": "draft",
    })


async def create_draft_lesson(ctx: ToolContext, command: DraftLessonInput) -> dict:
    actor = await _draft_actor(ctx)
    row = (await ctx.db_session.execute(
        select(Course, Chapter)
        .join(CourseChapter, (CourseChapter.course_id == Course.id) & (CourseChapter.org_id == Course.org_id))
        .join(Chapter, (Chapter.id == CourseChapter.chapter_id) & (Chapter.course_id == Course.id)
              & (Chapter.org_id == Course.org_id))
        .join(ResourceAuthor, ResourceAuthor.resource_uuid == Course.course_uuid)
        .where(
            Course.org_id == ctx.org.id, Course.course_uuid == command.course_uuid,
            Course.studytomy_managed.is_(True), Course.public.is_(False), Course.published.is_(False),
            Chapter.chapter_uuid == command.chapter_uuid, ResourceAuthor.user_id == actor,
            ResourceAuthor.authorship == ResourceAuthorshipEnum.CREATOR,
            ResourceAuthor.authorship_status == ResourceAuthorshipStatusEnum.ACTIVE,
        )
        .with_for_update(of=(Course, Chapter, ResourceAuthor))
    )).first()
    if row is None:
        raise HTTPException(404, "Draft course or chapter not found.")
    course, chapter = row
    await check_resource_access(ctx.request, ctx.db_session, ctx.user, course.course_uuid, AccessAction.CREATE)
    operation = "create_draft_lesson"
    replay = await _claim(ctx, actor, operation, command)
    if replay is not None:
        return replay.response_projection
    lesson = await create_activity(
        ctx.request,
        ActivityCreate(
            name=command.title, chapter_id=chapter.id, activity_type=ActivityTypeEnum.TYPE_DYNAMIC,
            activity_sub_type=ActivitySubTypeEnum.SUBTYPE_DYNAMIC_PAGE,
            content=lesson_text_document(command.text), published=False,
        ),
        ctx.user, ctx.db_session, transactional_draft=True,
    )
    return await _retain(ctx, actor, operation, command, "activity", lesson.activity_uuid, {
        "course_uuid": course.course_uuid, "chapter_uuid": chapter.chapter_uuid,
        "activity_uuid": lesson.activity_uuid, "status": "draft",
    })


SPECS = (
    ToolSpec(
        name="studytomy_create_draft_course",
        description="Create a private Studytomy draft course and its starter chapter. Use the same request ID to retry.",
        params_model=DraftCourseInput, tier=ActionTier.CREATE, rights_bucket="courses",
        access_action=AccessAction.CREATE, execute=create_draft_course,
        summarize=lambda params: f"Create private draft course {params.title!r}",
        approval=True, api_tokens=False, idempotent=True, audit_arguments=False,
    ),
    ToolSpec(
        name="studytomy_create_draft_lesson",
        description="Create an unpublished plain-text lesson in a Studytomy draft you own. Use the same request ID to retry.",
        params_model=DraftLessonInput, tier=ActionTier.CREATE, rights_bucket="courses",
        access_action=AccessAction.CREATE, execute=create_draft_lesson,
        summarize=lambda params: (
            f"Create unpublished lesson {params.title!r} in {params.course_uuid} / {params.chapter_uuid}"
        ),
        approval=True, api_tokens=False, idempotent=True, audit_arguments=False,
    ),
)
