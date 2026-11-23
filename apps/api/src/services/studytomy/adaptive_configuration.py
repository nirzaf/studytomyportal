"""Course-scoped staff navigation for adaptive configuration."""

from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import Activity
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.adaptive import StudyConcept, StudyCurriculumRevision
from src.db.studytomy.adaptive_diagnostics import StudyDiagnosticRevision
from src.db.studytomy.adaptive_items import StudyItemRevision
from src.db.users import PublicUser
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_studytomy_staff_action,
)
from src.services.studytomy.adaptive_access import authorize_adaptive_course_editor


class DefinitionChoice(BaseModel):
    uuid: str
    label: str
    version: int
    status: Literal["draft", "published", "retired"]
    curriculum_uuid: str
    concept_uuid: str | None = None
    independent: bool = False
    content_digest: str | None = None


class ConceptChoice(BaseModel):
    uuid: str
    title: str
    curriculum_uuid: str


class ActivityChoice(BaseModel):
    uuid: str
    name: str
    version: int


class AdaptiveConfiguration(BaseModel):
    can_author: bool
    can_review: bool
    curricula: list[DefinitionChoice]
    concepts: list[ConceptChoice]
    items: list[DefinitionChoice]
    diagnostics: list[DefinitionChoice]
    activities: list[ActivityChoice]


async def get_adaptive_configuration(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
) -> AdaptiveConfiguration:
    course = (
        await db.execute(
            select(Course).where(
                Course.course_uuid == course_uuid,
                Course.org_id == org.id,
            )
        )
    ).scalar_one_or_none()
    if course is None:
        raise HTTPException(404, "Course not found.")
    can_author = can_review = False
    try:
        await authorize_adaptive_course_editor(request, db, org, actor, course_uuid)
        can_author = True
    except HTTPException as error:
        if error.status_code not in (403, 404):
            raise
    try:
        await authorize_studytomy_staff_action(
            db,
            actor,
            org,
            StudytomyCapabilityName.CONTENT_RELEASE_REVIEW,
            target_scope_kind="course",
            target_resource_uuid=course_uuid,
            hide_existence_on_denial=True,
        )
        can_review = True
    except HTTPException as error:
        missing_review_policy = (
            error.status_code == 409
            and isinstance(error.detail, dict)
            and error.detail.get("code") == "POLICY_NOT_APPROVED"
        )
        if error.status_code not in (403, 404) and not (
            can_author and missing_review_policy
        ):
            raise
    if not (can_author or can_review):
        raise HTTPException(404, "Course not found.")

    definitions = (
        (
            await db.execute(
                select(StudyCurriculumRevision)
                .where(
                    StudyCurriculumRevision.org_id == org.id,
                    StudyCurriculumRevision.owner_course_uuid == course_uuid,
                )
                .order_by(StudyCurriculumRevision.id.desc())
            )
        )
        .scalars()
        .all()
    )
    by_id = {revision.id: revision.uuid for revision in definitions}
    concepts = (
        (
            await db.execute(
                select(StudyConcept)
                .where(
                    StudyConcept.org_id == org.id,
                    StudyConcept.curriculum_revision_id.in_(by_id),
                    StudyConcept.archived.is_(False),
                )
                .order_by(StudyConcept.sequence, StudyConcept.id)
            )
        )
        .scalars()
        .all()
    )
    items = (
        await db.execute(
            select(StudyItemRevision, StudyConcept.uuid)
            .join(
                StudyConcept,
                StudyConcept.id == StudyItemRevision.concept_id,
            )
            .where(
                StudyItemRevision.org_id == org.id,
                StudyItemRevision.curriculum_revision_id.in_(by_id),
                StudyItemRevision.course_uuid == course_uuid,
            )
            .order_by(StudyItemRevision.id.desc())
        )
    ).all()
    diagnostics = (
        (
            await db.execute(
                select(StudyDiagnosticRevision)
                .where(
                    StudyDiagnosticRevision.org_id == org.id,
                    StudyDiagnosticRevision.curriculum_revision_id.in_(by_id),
                )
                .order_by(StudyDiagnosticRevision.id.desc())
            )
        )
        .scalars()
        .all()
    )
    activities = []
    if can_author:
        activities = (
            (
                await db.execute(
                    select(Activity)
                    .where(
                        Activity.org_id == org.id,
                        Activity.course_id == course.id,
                    )
                    .order_by(Activity.name, Activity.id)
                )
            )
            .scalars()
            .all()
        )
    return AdaptiveConfiguration(
        can_author=can_author,
        can_review=can_review,
        curricula=[
            DefinitionChoice(
                uuid=row.uuid,
                label=f"{row.subject} · {row.level} · v{row.revision}",
                version=row.version,
                status=row.status,
                curriculum_uuid=row.uuid,
                content_digest=row.content_digest,
            )
            for row in definitions
        ],
        concepts=[
            ConceptChoice(
                uuid=row.uuid,
                title=row.title,
                curriculum_uuid=by_id[row.curriculum_revision_id],
            )
            for row in concepts
        ],
        items=[
            DefinitionChoice(
                uuid=row.uuid,
                label=row.public_question["prompt"],
                version=row.version,
                status=row.status,
                curriculum_uuid=by_id[row.curriculum_revision_id],
                concept_uuid=concept_uuid,
                independent=row.source_exposure == "private_original",
                content_digest=row.content_digest,
            )
            for row, concept_uuid in items
        ],
        diagnostics=[
            DefinitionChoice(
                uuid=row.uuid,
                label=row.name,
                version=row.version,
                status=row.status,
                curriculum_uuid=by_id[row.curriculum_revision_id],
                content_digest=row.content_digest,
            )
            for row in diagnostics
        ],
        activities=[
            ActivityChoice(
                uuid=row.activity_uuid, name=row.name, version=row.current_version
            )
            for row in activities
        ],
    )
