"""Private content snapshot inputs for managed course releases."""

import asyncio
import hashlib
import re
from contextlib import aclosing, suppress
from datetime import datetime
from tempfile import TemporaryFile
from typing import BinaryIO
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import Activity
from src.db.courses.assignments import Assignment, AssignmentTask
from src.db.courses.chapter_activities import ChapterActivity
from src.db.courses.chapters import Chapter
from src.db.courses.course_chapters import CourseChapter
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.governance import StudyPolicyKey, inspect_study_policy_body
from src.services.courses.transfer.storage_utils import delete_storage_file
from src.services.studytomy.errors import studytomy_http_error
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.learners import _current_policies
from src.services.utils.upload_content import open_content_stream, upload_content

_RELEASE_POLICIES = (
    StudyPolicyKey.D_08,
    StudyPolicyKey.D_09,
    StudyPolicyKey.D_13,
    StudyPolicyKey.D_16,
)
_RETENTION_CATEGORY = re.compile(r"[a-z][a-z0-9_]{0,63}")
_NATIVE_ACTIVITY_MEDIA = {
    "SUBTYPE_VIDEO_HOSTED": "video",
    "SUBTYPE_DOCUMENT_PDF": "documentpdf",
}
_TEXT_RELEASE_NODES = {
    "doc",
    "paragraph",
    "text",
    "heading",
    "hardBreak",
    "horizontalRule",
    "blockquote",
    "bulletList",
    "orderedList",
    "listItem",
    "codeBlock",
}
_SUPPORTED_RELEASE_MARKS = {
    "bold",
    "italic",
    "strike",
    "underline",
    "code",
    "subscript",
    "superscript",
}


def _release_media_unsupported() -> Exception:
    return studytomy_http_error(
        409,
        "RELEASE_MEDIA_UNSUPPORTED",
        "This course contains a media or lesson format that cannot be retained in a release.",
    )


def validate_release_media_coverage(snapshot: dict) -> None:
    """Refuse formats whose complete set of dependencies is not yet captured."""
    if any(chapter.get("thumbnail_image") for chapter in snapshot["chapters"]):
        raise _release_media_unsupported()
    for activity in snapshot["activities"]:
        subtype = activity["activity_sub_type"]
        if subtype == "SUBTYPE_VIDEO_HOSTED":
            metadata = activity.get("extra_metadata") or {}
            if metadata.get("captions") or metadata.get("thumbnails"):
                raise _release_media_unsupported()
        if subtype in {*_NATIVE_ACTIVITY_MEDIA, "SUBTYPE_ASSIGNMENT_ANY"}:
            continue
        if subtype != "SUBTYPE_DYNAMIC_PAGE":
            raise _release_media_unsupported()
        pending = [activity["content"]] if activity["content"] else []
        while pending:
            node = pending.pop()
            if (
                not isinstance(node, dict)
                or node.get("type") not in _TEXT_RELEASE_NODES
            ):
                raise _release_media_unsupported()
            marks = node.get("marks", [])
            if not isinstance(marks, list) or any(
                not isinstance(mark, dict)
                or mark.get("type") not in _SUPPORTED_RELEASE_MARKS
                for mark in marks
            ):
                raise _release_media_unsupported()
            children = node.get("content", [])
            if not isinstance(children, list):
                raise _release_media_unsupported()
            pending.extend(children)


async def _read_release_media(
    org_uuid: str,
    reference: str,
    *,
    destination: BinaryIO | None = None,
    size_limit: int | None = None,
) -> dict[str, int | str]:
    directory, filename = reference.rsplit("/", 1)
    digest = hashlib.sha256()
    size = 0
    async with aclosing(
        await open_content_stream(
            directory=directory,
            type_of_dir="orgs",
            uuid=org_uuid,
            file_and_format=filename,
        )
    ) as chunks:
        async for chunk in chunks:
            size += len(chunk)
            if size_limit is not None and size > size_limit:
                break
            digest.update(chunk)
            if destination is not None:
                await asyncio.to_thread(destination.write, chunk)
    return {"sha256": digest.hexdigest(), "size_bytes": size}


async def retain_reviewed_release_media(
    org_uuid: str,
    approved_manifest: dict[str, dict[str, int | str]],
) -> dict[str, dict[str, int | str]]:
    """Copy verified bytes to distinct private keys, compensating a partial copy."""
    directory = f"studytomy/releases/{uuid4()}"
    retained = {}
    attempted = []
    try:
        for source, expected in sorted(approved_manifest.items()):
            if not source.startswith("courses/"):
                raise studytomy_http_error(
                    409,
                    "RELEASE_MEDIA_SOURCE_INVALID",
                    "Release media must belong to a course.",
                )
            with TemporaryFile(mode="w+b") as buffer:
                observed = await _read_release_media(
                    org_uuid,
                    source,
                    destination=buffer,
                    size_limit=int(expected["size_bytes"]),
                )
                if observed != expected:
                    raise studytomy_http_error(
                        409,
                        "REVIEW_MEDIA_CHANGED",
                        "Stored media changed after review.",
                    )
                await asyncio.to_thread(buffer.seek, 0)
                # A binary suffix prevents the stock upload adapter from rewriting video bytes.
                filename = f"{uuid4()}.bin"
                reference = f"{directory}/{filename}"
                attempted.append(reference)
                await upload_content(
                    directory=directory,
                    type_of_dir="orgs",
                    uuid=org_uuid,
                    file_binary=buffer,
                    file_and_format=filename,
                )
            if (
                await _read_release_media(
                    org_uuid,
                    reference,
                    size_limit=int(expected["size_bytes"]),
                )
                != expected
            ):
                raise studytomy_http_error(
                    503,
                    "RELEASE_STORAGE_INVALID",
                    "Private release storage verification failed.",
                )
            retained[source] = {**expected, "storage_reference": reference}
    except Exception:
        for reference in attempted:
            with suppress(Exception):
                await asyncio.to_thread(
                    delete_storage_file, f"content/orgs/{org_uuid}/{reference}"
                )
        raise
    return retained


async def discard_retained_release_media(
    org_uuid: str,
    retained: dict[str, dict[str, int | str]],
) -> None:
    """Compensate only fresh private copies before a release commit begins."""
    for item in retained.values():
        reference = item["storage_reference"]
        with suppress(Exception):
            await asyncio.to_thread(
                delete_storage_file, f"content/orgs/{org_uuid}/{reference}"
            )


class ReleasePolicyVersion(BaseModel):
    policy_id: int
    policy_uuid: str
    version: int
    body_sha256: str


class ReleasePolicyBinding(BaseModel):
    versions: dict[StudyPolicyKey, ReleasePolicyVersion]
    retention_category: str


async def load_release_policy_binding(
    db: AsyncSession,
    org: Organization,
    now: datetime,
) -> ReleasePolicyBinding:
    """Pin currently approved decisions without interpreting their course applicability."""
    policies = await _current_policies(db, org, _RELEASE_POLICIES, now)
    retention = next(
        policy for policy in policies if policy.policy_key == StudyPolicyKey.D_13.value
    )
    body, _ = inspect_study_policy_body(StudyPolicyKey.D_13, retention.body)
    rules = body.specified_value("retention") if body else None
    category = (
        rules.get("course_release_retention_category")
        if isinstance(rules, dict)
        else None
    )
    if not isinstance(category, str) or not _RETENTION_CATEGORY.fullmatch(category):
        raise studytomy_http_error(
            409,
            "RELEASE_RETENTION_UNCONFIGURED",
            "Current retention policy must specify a course release category.",
        )
    return ReleasePolicyBinding(
        versions={
            StudyPolicyKey(policy.policy_key): ReleasePolicyVersion(
                policy_id=policy.id,
                policy_uuid=policy.policy_uuid,
                version=policy.version,
                body_sha256=canonical_request_hash(policy.body),
            )
            for policy in policies
        },
        retention_category=category,
    )


async def capture_native_release_media(
    org_uuid: str,
    snapshot: dict,
) -> dict[str, dict[str, int | str]]:
    """Hash first-class native media references from the private candidate payload."""
    course = snapshot["course"]
    root = f"courses/{course['course_uuid']}"
    activities = {activity["id"]: activity for activity in snapshot["activities"]}
    assignments = {
        assignment["id"]: assignment for assignment in snapshot["assignments"]
    }
    references = set()
    for field in ("thumbnail_image", "thumbnail_video"):
        if course.get(field):
            references.add((f"{root}/thumbnails", course[field]))
    for activity in activities.values():
        directory = _NATIVE_ACTIVITY_MEDIA.get(activity["activity_sub_type"])
        if directory:
            content = activity["content"]
            filename = content.get("filename") if isinstance(content, dict) else None
            if (
                not isinstance(filename, str)
                or not filename
                or filename in _NATIVE_ACTIVITY_MEDIA.values()
            ):
                raise studytomy_http_error(
                    409,
                    "RELEASE_MEDIA_MISSING",
                    "A native course activity has no stored media.",
                )
            references.add(
                (f"{root}/activities/{activity['activity_uuid']}/{directory}", filename)
            )
    for assignment in assignments.values():
        if assignment.get("solution_file"):
            activity = activities.get(assignment["activity_id"])
            if activity is None:
                raise studytomy_http_error(
                    409,
                    "RELEASE_MEDIA_SOURCE_INVALID",
                    "Assignment media has no matching activity.",
                )
            directory = f"{root}/activities/{activity['activity_uuid']}/assignments/{assignment['assignment_uuid']}/solution"
            references.add((directory, assignment["solution_file"]))
    for task in snapshot["assignment_tasks"]:
        if task.get("reference_file"):
            assignment = assignments.get(task["assignment_id"])
            activity = activities.get(assignment["activity_id"]) if assignment else None
            if activity is None or task["activity_id"] != activity["id"]:
                raise studytomy_http_error(
                    409,
                    "RELEASE_MEDIA_SOURCE_INVALID",
                    "Task media has no matching activity.",
                )
            directory = (
                f"{root}/activities/{activity['activity_uuid']}/assignments/{assignment['assignment_uuid']}"
                f"/tasks/{task['assignment_task_uuid']}"
            )
            references.add((directory, task["reference_file"]))

    return {
        reference: await _read_release_media(org_uuid, reference)
        for reference in sorted(
            f"{directory}/{filename}" for directory, filename in references
        )
    }


async def course_candidate_snapshot(
    db: AsyncSession, course: Course
) -> dict[str, object]:
    """Capture private candidate content, including native grading inputs and references."""
    chapters = (
        (
            await db.execute(
                select(Chapter)
                .where(
                    Chapter.org_id == course.org_id,
                    Chapter.course_id == course.id,
                )
                .order_by(Chapter.chapter_uuid, Chapter.id)
            )
        )
        .scalars()
        .all()
    )
    activities = (
        (
            await db.execute(
                select(Activity)
                .where(
                    Activity.org_id == course.org_id,
                    Activity.course_id == course.id,
                )
                .order_by(Activity.activity_uuid, Activity.id)
            )
        )
        .scalars()
        .all()
    )
    chapter_order = (
        await db.execute(
            select(
                CourseChapter.chapter_id,
                CourseChapter.order,
            )
            .where(
                CourseChapter.org_id == course.org_id,
                CourseChapter.course_id == course.id,
            )
            .order_by(CourseChapter.order, CourseChapter.chapter_id)
        )
    ).all()
    activity_order = (
        await db.execute(
            select(
                ChapterActivity.chapter_id,
                ChapterActivity.activity_id,
                ChapterActivity.order,
            )
            .where(
                ChapterActivity.org_id == course.org_id,
                ChapterActivity.course_id == course.id,
            )
            .order_by(
                ChapterActivity.chapter_id,
                ChapterActivity.order,
                ChapterActivity.activity_id,
            )
        )
    ).all()
    assignments = (
        (
            await db.execute(
                select(Assignment)
                .where(
                    Assignment.org_id == course.org_id,
                    Assignment.course_id == course.id,
                )
                .order_by(Assignment.assignment_uuid, Assignment.id)
            )
        )
        .scalars()
        .all()
    )
    tasks = (
        (
            await db.execute(
                select(AssignmentTask)
                .join(
                    Assignment,
                    Assignment.id == AssignmentTask.assignment_id,
                )
                .where(
                    Assignment.org_id == course.org_id,
                    Assignment.course_id == course.id,
                    AssignmentTask.org_id == course.org_id,
                    AssignmentTask.course_id == course.id,
                )
                .order_by(AssignmentTask.assignment_task_uuid, AssignmentTask.id)
            )
        )
        .scalars()
        .all()
    )
    volatile_fields = {"creation_date", "update_date"}
    return {
        "schema": "course_content_evidence_v4",
        "course": course.model_dump(
            mode="json", exclude={"creation_date", "update_date", "studytomy_managed"}
        ),
        "chapters": [
            chapter.model_dump(mode="json", exclude={"creation_date", "update_date"})
            for chapter in chapters
        ],
        "chapter_order": [list(row) for row in chapter_order],
        "activity_order": [list(row) for row in activity_order],
        "activities": [
            activity.model_dump(
                mode="json",
                exclude={"creation_date", "update_date", "last_modified_by_id"},
            )
            for activity in activities
        ],
        "assignments": [
            assignment.model_dump(mode="json", exclude=volatile_fields)
            for assignment in assignments
        ],
        "assignment_tasks": [
            task.model_dump(mode="json", exclude=volatile_fields) for task in tasks
        ],
    }
