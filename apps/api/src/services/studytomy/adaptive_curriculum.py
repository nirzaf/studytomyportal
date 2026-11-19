"""Scoped creation and editing of draft curriculum definitions."""

from datetime import UTC, datetime
from graphlib import CycleError, TopologicalSorter
from typing import Annotated, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import Activity
from src.db.organizations import Organization
from src.db.studytomy.adaptive import (
    CurriculumDraftRead,
    StudyConcept,
    StudyConceptActivity,
    StudyConceptPrerequisite,
    StudyCurriculumRevision,
)
from src.db.studytomy.adaptive_items import StudyItemRevision
from src.db.users import PublicUser
from src.services.studytomy.adaptive_access import authorize_adaptive_course_editor
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.errors import STALE_VERSION, studytomy_http_error
from src.services.studytomy.idempotency import (
    canonical_request_hash,
    claim_idempotency,
    complete_idempotency,
)
from src.services.studytomy.policies import lock_organization

Code = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$")]
Label = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]
Reference = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
]
Version = Annotated[int, Field(strict=True, ge=1, le=2_147_483_646)]
_CREATE_OPERATION = "studytomy.adaptive.curriculum.created"


class ProgrammeMetadata(BaseModel):
    """Optional verified official syllabus identity & cohort coverage metadata."""

    model_config = ConfigDict(extra="forbid")

    authority: Label
    syllabus_edition: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
    ]
    effective_cohort: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
    ]
    stage: Literal["junior_secondary", "gce_ol", "gce_al", "other"] = "other"
    coverage_status: Literal[
        "unverified", "partial", "supplementary_only", "verified_complete"
    ] = "unverified"
    source_language: Annotated[
        str, StringConstraints(pattern=r"^[A-Za-z0-9-]{1,32}$")
    ] | None = None


class CurriculumFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: Label
    level: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
    ]
    language: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9-]{1,32}$")]
    jurisdiction: Label | None = None
    source_reference: Reference
    rights_reference: Reference
    programme_metadata: ProgrammeMetadata | None = None


class CurriculumDraftCreate(CurriculumFields):
    curriculum_key: Code
    revision: Version = 1
    owner_course_uuid: Annotated[str, StringConstraints(min_length=1, max_length=255)]


class CurriculumDraftUpdate(CurriculumFields):
    expected_version: Version


class ConceptDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Code
    title: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
    ]
    objective: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
    ]
    sequence: Annotated[int, Field(strict=True, ge=0, le=2048)]


class PrerequisiteDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concept_code: Code
    prerequisite_code: Code


class CurriculumGraphUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Version
    concepts: Annotated[list[ConceptDraft], Field(min_length=1, max_length=100)]
    prerequisites: Annotated[list[PrerequisiteDraft], Field(max_length=500)]


class ConceptRead(ConceptDraft):
    model_config = ConfigDict(from_attributes=True)

    uuid: str


class CurriculumGraphRead(BaseModel):
    curriculum: CurriculumDraftRead
    concepts: list[ConceptRead]
    prerequisites: list[PrerequisiteDraft]


class LessonMappingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    course_uuid: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    activity_uuid: Annotated[str, StringConstraints(min_length=1, max_length=89)]
    activity_version: Version
    usage: Literal["teach", "practice", "review"]


class ConceptMappingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Version
    mappings: Annotated[list[LessonMappingInput], Field(max_length=50)]


class ConceptMappingRead(BaseModel):
    curriculum: CurriculumDraftRead
    concept_uuid: str
    mappings: list[LessonMappingInput]


async def create_curriculum_draft(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    draft: CurriculumDraftCreate,
    idempotency_key: str,
) -> CurriculumDraftRead:
    await lock_organization(db, org.id)
    course = await authorize_adaptive_course_editor(
        request, db, org, actor, draft.owner_course_uuid
    )
    replay = await claim_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=_CREATE_OPERATION,
        idempotency_key=idempotency_key,
        request_payload=draft.model_dump(mode="json"),
    )
    if replay is not None:
        try:
            return CurriculumDraftRead.model_validate(replay.response_projection)
        except ValidationError as error:
            raise HTTPException(
                409, "The original curriculum response is unavailable."
            ) from error

    existing = (
        await db.execute(
            select(StudyCurriculumRevision.id).where(
                StudyCurriculumRevision.org_id == org.id,
                StudyCurriculumRevision.curriculum_key == draft.curriculum_key,
                StudyCurriculumRevision.revision == draft.revision,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await db.rollback()
        raise studytomy_http_error(
            409,
            "CURRICULUM_REVISION_EXISTS",
            "That curriculum revision already exists.",
        )

    row = StudyCurriculumRevision(org_id=org.id, **draft.model_dump())
    db.add(row)
    await db.flush()
    result = CurriculumDraftRead.model_validate(row)
    await append_business_event(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        subject_uuid=course.course_uuid,
        action=_CREATE_OPERATION,
        reason_code="CURRICULUM_DRAFT_CREATED",
        resource_kind="curriculum",
        resource_uuid=row.uuid,
        after_state={"status": "draft", "version": row.version},
    )
    await complete_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=_CREATE_OPERATION,
        idempotency_key=idempotency_key,
        resource_kind="curriculum",
        resource_uuid=row.uuid,
        response_status=201,
        response_projection=result.model_dump(mode="json"),
    )
    await db.commit()
    return result


async def _locked_draft(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    revision_uuid: str,
    expected_version: int,
) -> StudyCurriculumRevision:
    await lock_organization(db, org.id)
    row = (
        await db.execute(
            select(StudyCurriculumRevision)
            .where(
                StudyCurriculumRevision.org_id == org.id,
                StudyCurriculumRevision.uuid == revision_uuid,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.owner_course_uuid is None:
        raise HTTPException(404, "Curriculum revision not found.")
    await authorize_adaptive_course_editor(
        request, db, org, actor, row.owner_course_uuid
    )
    if row.status != "draft" or row.version != expected_version:
        raise studytomy_http_error(
            409, STALE_VERSION, "Reload the current curriculum revision."
        )
    return row


def _advance_draft(row: StudyCurriculumRevision) -> CurriculumDraftRead:
    row.version += 1
    row.updated_at = datetime.now(UTC)
    return CurriculumDraftRead.model_validate(row)


async def update_curriculum_draft(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    revision_uuid: str,
    draft: CurriculumDraftUpdate,
) -> CurriculumDraftRead:
    row = await _locked_draft(
        request, db, org, actor, revision_uuid, draft.expected_version
    )
    row.sqlmodel_update(draft.model_dump(exclude={"expected_version"}))
    result = _advance_draft(row)
    await db.commit()
    return result


def _validate_graph(graph: CurriculumGraphUpdate) -> None:
    codes = {concept.code for concept in graph.concepts}
    edges = {
        (edge.concept_code, edge.prerequisite_code) for edge in graph.prerequisites
    }
    if len(codes) != len(graph.concepts) or len(edges) != len(graph.prerequisites):
        raise studytomy_http_error(
            422,
            "CURRICULUM_GRAPH_DUPLICATE",
            "Concepts and prerequisites must be unique.",
        )
    sorter = TopologicalSorter()
    for code, parent in edges:
        if code not in codes or parent not in codes:
            raise studytomy_http_error(
                422,
                "CURRICULUM_GRAPH_REFERENCE",
                "Prerequisites must reference this revision.",
            )
        sorter.add(code, parent)
    try:
        sorter.prepare()
    except CycleError as error:
        raise studytomy_http_error(
            422, "CURRICULUM_GRAPH_CYCLE", "Prerequisites must be acyclic."
        ) from error


async def replace_curriculum_graph(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    revision_uuid: str,
    graph: CurriculumGraphUpdate,
) -> CurriculumGraphRead:
    row = await _locked_draft(
        request, db, org, actor, revision_uuid, graph.expected_version
    )
    _validate_graph(graph)
    current = (
        (
            await db.execute(
                select(StudyConcept).where(
                    StudyConcept.org_id == org.id,
                    StudyConcept.curriculum_revision_id == row.id,
                )
            )
        )
        .scalars()
        .all()
    )
    existing = {concept.code: concept for concept in current}
    codes = {draft.code for draft in graph.concepts}
    removed = [concept.id for concept in current if concept.code not in codes]
    if (
        removed
        and (
            await db.execute(
                select(StudyConceptActivity.id)
                .where(StudyConceptActivity.concept_id.in_(removed))
                .limit(1)
            )
        ).scalar_one_or_none()
        is not None
    ):
        raise studytomy_http_error(
            409,
            "CURRICULUM_CONCEPT_MAPPED",
            "Remove the lesson mappings before removing their concept.",
        )
    if (
        removed
        and (
            await db.execute(
                select(StudyItemRevision.id)
                .where(StudyItemRevision.concept_id.in_(removed))
                .limit(1)
            )
        ).scalar_one_or_none()
        is not None
    ):
        raise studytomy_http_error(
            409,
            "CURRICULUM_CONCEPT_HAS_ITEMS",
            "Create a new curriculum revision to remove a concept with retained assessment items.",
        )
    await db.execute(
        delete(StudyConceptPrerequisite).where(
            StudyConceptPrerequisite.org_id == org.id,
            StudyConceptPrerequisite.curriculum_revision_id == row.id,
        )
    )
    concepts = {}
    for draft in graph.concepts:
        concept = existing.pop(draft.code, None)
        if concept is None:
            concept = StudyConcept(
                org_id=org.id, curriculum_revision_id=row.id, **draft.model_dump()
            )
            db.add(concept)
        else:
            concept.sqlmodel_update(draft.model_dump())
        concepts[draft.code] = concept
    if existing:
        for concept in existing.values():
            await db.delete(concept)
    await db.flush()
    db.add_all(
        [
            StudyConceptPrerequisite(
                org_id=org.id,
                curriculum_revision_id=row.id,
                concept_id=concepts[edge.concept_code].id,
                prerequisite_id=concepts[edge.prerequisite_code].id,
            )
            for edge in graph.prerequisites
        ]
    )
    # Flush child inserts before updating the root version, because PostgreSQL
    # child guards also lock and touch the root while it is still a draft.
    await db.flush()
    result = CurriculumGraphRead(
        curriculum=_advance_draft(row),
        concepts=[ConceptRead.model_validate(concept) for concept in concepts.values()],
        prerequisites=graph.prerequisites,
    )
    await db.commit()
    return result


def curriculum_activity_snapshot_digest(activity: dict[str, object]) -> str:
    """Hash the stable source fields from either a live row or frozen release."""
    return canonical_request_hash(
        {
            "schema": "adaptive_lesson_source_v1",
            "activity": {
                key: value
                for key, value in activity.items()
                if key
                not in {"id", "creation_date", "update_date", "last_modified_by_id"}
            },
        }
    )


def curriculum_activity_digest(activity: Activity) -> str:
    return curriculum_activity_snapshot_digest(
        activity.model_dump(mode="json")
    )


async def replace_concept_mappings(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    concept_uuid: str,
    update: ConceptMappingUpdate,
) -> ConceptMappingRead:
    result = await db.execute(
        select(StudyConcept, StudyCurriculumRevision.uuid)
        .join(
            StudyCurriculumRevision,
            StudyCurriculumRevision.id == StudyConcept.curriculum_revision_id,
        )
        .where(StudyConcept.org_id == org.id, StudyConcept.uuid == concept_uuid)
    )
    pair = result.one_or_none()
    if pair is None:
        raise HTTPException(404, "Concept not found.")
    concept, revision_uuid = pair
    row = await _locked_draft(
        request, db, org, actor, revision_uuid, update.expected_version
    )
    # A competing graph editor may have removed the concept before the lock.
    concept_id = (
        await db.execute(
            select(StudyConcept.id).where(
                StudyConcept.id == concept.id,
                StudyConcept.curriculum_revision_id == row.id,
            )
        )
    ).scalar_one_or_none()
    if concept_id is None:
        raise HTTPException(404, "Concept not found.")

    mapping_keys = {(m.activity_uuid, m.usage) for m in update.mappings}
    if len(mapping_keys) != len(update.mappings):
        raise studytomy_http_error(
            422, "CURRICULUM_MAPPING_DUPLICATE", "Each lesson and use must be unique."
        )
    previous = set(
        (
            await db.execute(
                select(StudyConceptActivity.course_uuid).where(
                    StudyConceptActivity.concept_id == concept_id,
                )
            )
        ).scalars()
    )
    course_uuids = previous | {m.course_uuid for m in update.mappings}
    courses = {
        uuid: await authorize_adaptive_course_editor(request, db, org, actor, uuid)
        for uuid in sorted(course_uuids)
    }
    mappings = []
    for mapping in sorted(
        update.mappings, key=lambda m: (m.course_uuid, m.activity_uuid, m.usage)
    ):
        course = courses[mapping.course_uuid]
        matches = (
            (
                await db.execute(
                    select(Activity)
                    .where(
                        Activity.activity_uuid == mapping.activity_uuid,
                        Activity.course_id == course.id,
                        Activity.org_id == org.id,
                    )
                    .limit(2)
                    .with_for_update(read=True)
                )
            )
            .scalars()
            .all()
        )
        if len(matches) != 1:
            raise HTTPException(404, "Lesson not found.")
        activity = matches[0]
        if activity.current_version != mapping.activity_version:
            raise studytomy_http_error(
                409, STALE_VERSION, "Reload the current lesson revision."
            )
        mappings.append(
            StudyConceptActivity(
                org_id=org.id,
                curriculum_revision_id=row.id,
                concept_id=concept_id,
                course_id=course.id,
                activity_id=activity.id,
                activity_digest=curriculum_activity_digest(activity),
                **mapping.model_dump(),
            )
        )
    await db.execute(
        delete(StudyConceptActivity).where(
            StudyConceptActivity.concept_id == concept_id
        )
    )
    db.add_all(mappings)
    await db.flush()
    result = ConceptMappingRead(
        curriculum=_advance_draft(row),
        concept_uuid=concept_uuid,
        mappings=update.mappings,
    )
    await db.commit()
    return result
