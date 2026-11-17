"""Create review candidates and versioned draft metadata for adaptive items."""

import re
from datetime import UTC, datetime
from decimal import localcontext
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.adaptive import StudyConcept, StudyCurriculumRevision
from src.db.studytomy.adaptive_items import StudyItemRevision
from src.db.users import PublicUser
from src.services.studytomy.adaptive_access import authorize_adaptive_course_editor
from src.services.studytomy.adaptive_curriculum import Reference, Version
from src.services.studytomy.adaptive_curriculum_review import Digest
from src.services.studytomy.adaptive_item_source import (
    ChoiceQuestion,
    ItemKind,
    ItemSourceRead,
    PublicQuestion,
    SourceExposure,
    SourceLocator,
    parse_item_decimal,
    read_native_item_source,
)
from src.services.studytomy.adaptive_objective_scoring import OBJECTIVE_EVALUATOR
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.errors import STALE_VERSION, studytomy_http_error
from src.services.studytomy.idempotency import (
    canonical_request_hash,
    claim_idempotency,
    complete_idempotency,
)
from src.services.studytomy.policies import lock_organization

_CREATE_OPERATION = "studytomy.adaptive.item.created"
_QUESTION = TypeAdapter(PublicQuestion)
_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
ConceptUUID = Annotated[str, StringConstraints(pattern=r"^concept_[0-9a-f-]{36}$")]
FamilyUUID = Annotated[str, StringConstraints(pattern=r"^item_family_[0-9a-f-]{36}$")]


class ItemConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    difficulty: Annotated[int, Field(strict=True, ge=1, le=5)]
    hint_ladder: Annotated[
        list[
            Annotated[
                str,
                StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
            ]
        ],
        Field(max_length=5),
    ] = Field(default_factory=list)
    error_codes: Annotated[dict[SourceLocator, str], Field(max_length=12)] = Field(
        default_factory=dict
    )


class ItemDraftCreate(ItemConfiguration):
    concept_uuid: ConceptUUID
    item_family_uuid: FamilyUUID | None = None
    revision: Version = 1


class ItemRevisionCreate(ItemDraftCreate):
    source_task_uuid: SourceLocator
    source_question_uuid: SourceLocator | None = None
    expected_source_digest: Digest


class OriginalChoiceAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["single_choice"]
    correct_option_uuid: SourceLocator


class OriginalNumberAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["number"]
    expected: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    tolerance: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class OriginalItemRevisionCreate(ItemDraftCreate):
    original_question: PublicQuestion
    original_answer: Annotated[
        OriginalChoiceAnswer | OriginalNumberAnswer, Field(discriminator="kind")
    ]
    original_rights_reference: Reference


class ItemRevisionUpdate(ItemConfiguration):
    expected_version: Version


class ItemRevisionStaffRead(BaseModel):
    uuid: str
    item_family_uuid: str
    concept_uuid: str
    revision: int
    version: int
    status: Literal["draft", "published", "retired"]
    kind: ItemKind
    difficulty: int
    source_task_uuid: str | None
    source_question_uuid: str | None
    source_digest: Digest
    source_exposure: SourceExposure
    independent_diagnostic_eligible: bool = False
    question: PublicQuestion
    hint_ladder: list[str]
    error_codes: dict[str, str]


def _configuration(
    payload: ItemConfiguration,
    question: PublicQuestion,
    private_answer: dict,
) -> dict:
    allowed_keys = (
        {option.option_uuid for option in question.options}
        - {private_answer["correct_option_uuid"]}
        if isinstance(question, ChoiceQuestion)
        else {"incorrect"}
    )
    if any(
        key not in allowed_keys or _ERROR_CODE.fullmatch(value) is None
        for key, value in payload.error_codes.items()
    ):
        raise studytomy_http_error(
            422,
            "ITEM_ERROR_CODE_INVALID",
            "Error codes must name valid reviewed distractors.",
        )
    return payload.model_dump(include={"difficulty", "hint_ladder", "error_codes"})


def _project(row: StudyItemRevision, concept_uuid: str) -> ItemRevisionStaffRead:
    return ItemRevisionStaffRead(
        uuid=row.uuid,
        item_family_uuid=row.item_family_uuid,
        concept_uuid=concept_uuid,
        revision=row.revision,
        version=row.version,
        status=row.status,
        kind=row.kind,
        difficulty=row.difficulty,
        source_task_uuid=row.source_task_uuid,
        source_question_uuid=row.source_question_uuid,
        source_digest=row.source_digest,
        source_exposure=row.source_exposure,
        independent_diagnostic_eligible=row.source_exposure == "private_original"
        and row.status == "published",
        question=_QUESTION.validate_python(row.public_question),
        hint_ladder=row.hint_ladder,
        error_codes=row.error_codes,
    )


async def _authorize_item_parent(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    curriculum: StudyCurriculumRevision,
) -> None:
    if not curriculum.owner_course_uuid or curriculum.status == "retired":
        raise HTTPException(404, "Curriculum concept not found.")
    await authorize_adaptive_course_editor(
        request, db, org, actor, curriculum.owner_course_uuid
    )


def _original_source(
    payload: OriginalItemRevisionCreate,
    course_uuid: str,
) -> tuple[ItemSourceRead, dict]:
    question = payload.original_question
    answer = payload.original_answer
    rubric = answer.model_dump(exclude={"kind"})
    canonical_rubric = rubric
    try:
        if isinstance(question, ChoiceQuestion) and isinstance(
            answer, OriginalChoiceAnswer
        ):
            choices = {option.option_uuid for option in question.options}
            if (
                len(choices) != len(question.options)
                or answer.correct_option_uuid not in choices
            ):
                raise ValueError("The key must name exactly one unique option.")
        elif not isinstance(question, ChoiceQuestion) and isinstance(
            answer, OriginalNumberAnswer
        ):
            expected = parse_item_decimal(answer.expected)
            tolerance = parse_item_decimal(answer.tolerance)
            if tolerance < 0:
                raise ValueError("Tolerance must be nonnegative.")
            # Hash equivalent decimals identically while retaining parseable bounded strings.
            with localcontext() as context:
                context.prec = 256
                canonical_rubric = {
                    "expected": str(expected.normalize()) if expected else "0",
                    "tolerance": str(tolerance.normalize()) if tolerance else "0",
                }
        else:
            raise TypeError("The key and question kinds must match.")
    except (TypeError, ValueError) as error:
        raise studytomy_http_error(
            422,
            "ITEM_ORIGINAL_RUBRIC_INVALID",
            "Provide a valid key for the original question.",
        ) from error
    digest = canonical_request_hash(
        {
            "schema": "adaptive_original_item_v1",
            "question": question.model_dump(mode="json"),
            "answer": canonical_rubric,
        }
    )
    return ItemSourceRead(
        source_task_uuid=None,
        source_question_uuid=None,
        assignment_uuid=None,
        course_uuid=course_uuid,
        source_digest=digest,
        source_exposure="private_original",
        question=question,
    ), rubric


async def create_item_revision(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    payload: ItemRevisionCreate | OriginalItemRevisionCreate,
    idempotency_key: str,
) -> ItemRevisionStaffRead:
    await lock_organization(db, org.id)
    result = await db.execute(
        select(StudyConcept, StudyCurriculumRevision)
        .join(
            StudyCurriculumRevision,
            StudyCurriculumRevision.id == StudyConcept.curriculum_revision_id,
        )
        .where(
            StudyConcept.org_id == org.id,
            StudyConcept.uuid == payload.concept_uuid,
            StudyConcept.archived.is_(False),
        )
        .with_for_update()
    )
    pair = result.one_or_none()
    if pair is None:
        raise HTTPException(404, "Curriculum concept not found.")
    concept, curriculum = pair
    await _authorize_item_parent(request, db, org, actor, curriculum)
    if isinstance(payload, OriginalItemRevisionCreate):
        source, answer = _original_source(payload, curriculum.owner_course_uuid)
        rights_reference = payload.original_rights_reference
        family_query = StudyItemRevision.source_digest == source.source_digest
    else:
        source, answer = await read_native_item_source(
            request,
            db,
            org,
            actor,
            payload.source_task_uuid,
            payload.source_question_uuid,
            lock=True,
        )
        rights_reference = curriculum.rights_reference
        family_query = (
            StudyItemRevision.source_task_uuid == source.source_task_uuid
        ) & (StudyItemRevision.source_question_uuid == source.source_question_uuid)
    replay = await claim_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=_CREATE_OPERATION,
        idempotency_key=idempotency_key,
        request_payload=payload.model_dump(mode="json"),
    )
    if replay is not None:
        return ItemRevisionStaffRead.model_validate(replay.response_projection)
    if (
        isinstance(payload, ItemRevisionCreate)
        and source.source_digest != payload.expected_source_digest
    ):
        raise studytomy_http_error(
            409, STALE_VERSION, "Reload the current native assignment question."
        )
    options = _configuration(payload, source.question, answer)
    known_families = set(
        (
            await db.execute(
                select(StudyItemRevision.item_family_uuid)
                .where(
                    StudyItemRevision.org_id == org.id,
                    family_query,
                )
                .distinct()
            )
        ).scalars()
    )
    family = (
        payload.item_family_uuid
        or next(iter(known_families), None)
        or f"item_family_{uuid4()}"
    )
    if known_families and known_families != {family}:
        raise studytomy_http_error(
            409,
            "ITEM_FAMILY_CONFLICT",
            "Use the existing item family for that question.",
        )
    previous = (
        await db.execute(
            select(StudyItemRevision.revision, StudyItemRevision.source_exposure)
            .where(
                StudyItemRevision.org_id == org.id,
                StudyItemRevision.item_family_uuid == family,
            )
            .order_by(StudyItemRevision.revision.desc())
            .limit(1)
        )
    ).one_or_none()
    if previous is not None and (
        (previous.source_exposure == "private_original")
        != (source.source_exposure == "private_original")
    ):
        raise studytomy_http_error(
            409,
            "ITEM_FAMILY_CONFLICT",
            "Private originals and native imports require separate item families.",
        )
    if payload.revision != (previous.revision if previous is not None else 0) + 1:
        raise studytomy_http_error(
            409, STALE_VERSION, "Use the next item family revision."
        )
    row = StudyItemRevision(
        org_id=org.id,
        curriculum_revision_id=curriculum.id,
        concept_id=concept.id,
        item_family_uuid=family,
        revision=payload.revision,
        kind=source.question.kind,
        course_uuid=source.course_uuid,
        assignment_uuid=source.assignment_uuid,
        source_task_uuid=source.source_task_uuid,
        source_question_uuid=source.source_question_uuid,
        source_digest=source.source_digest,
        source_exposure=source.source_exposure,
        evaluator_version=OBJECTIVE_EVALUATOR,
        rights_reference=rights_reference,
        public_question=source.question.model_dump(mode="json"),
        private_answer=answer,
        **options,
    )
    db.add(row)
    await db.flush()
    read = _project(row, concept.uuid)
    await append_business_event(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        action=_CREATE_OPERATION,
        reason_code="ITEM_DRAFT_CREATED",
        resource_kind="item_revision",
        resource_uuid=row.uuid,
        after_state={"status": "draft", "version": row.version},
    )
    await complete_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=_CREATE_OPERATION,
        idempotency_key=idempotency_key,
        resource_kind="item_revision",
        resource_uuid=row.uuid,
        response_status=201,
        response_projection=read.model_dump(mode="json"),
    )
    await db.commit()
    return read


async def update_item_revision(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    item_uuid: str,
    payload: ItemRevisionUpdate,
) -> ItemRevisionStaffRead:
    await lock_organization(db, org.id)
    result = await db.execute(
        select(StudyItemRevision, StudyConcept, StudyCurriculumRevision)
        .join(StudyConcept, StudyConcept.id == StudyItemRevision.concept_id)
        .join(
            StudyCurriculumRevision,
            StudyCurriculumRevision.id == StudyItemRevision.curriculum_revision_id,
        )
        .where(
            StudyItemRevision.org_id == org.id,
            StudyItemRevision.uuid == item_uuid,
            StudyConcept.archived.is_(False),
        )
        .with_for_update()
    )
    match = result.one_or_none()
    if match is None:
        raise HTTPException(404, "Item revision not found.")
    row, concept, curriculum = match
    await _authorize_item_parent(request, db, org, actor, curriculum)
    await authorize_adaptive_course_editor(request, db, org, actor, row.course_uuid)
    if row.status != "draft" or row.version != payload.expected_version:
        raise studytomy_http_error(
            409, STALE_VERSION, "Reload the current item revision."
        )
    row.sqlmodel_update(
        _configuration(
            payload, _QUESTION.validate_python(row.public_question), row.private_answer
        )
    )
    row.version += 1
    row.updated_at = datetime.now(UTC)
    read = _project(row, concept.uuid)
    await db.commit()
    return read
