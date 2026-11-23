"""Author and publish immutable, reviewed diagnostic blueprints."""

from collections import Counter
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.adaptive import (
    StudyConcept,
    StudyConceptPrerequisite,
    StudyCurriculumRevision,
)
from src.db.studytomy.adaptive_diagnostics import StudyDiagnosticRevision
from src.db.studytomy.adaptive_items import StudyItemRevision
from src.db.studytomy.governance import (
    StudyPolicyBody,
    StudyPolicyKey,
    StudyPolicyVersion,
)
from src.db.users import PublicUser
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_studytomy_staff_action,
)
from src.services.studytomy.adaptive_access import authorize_adaptive_course_editor
from src.services.studytomy.adaptive_curriculum import Label, Reference, Version
from src.services.studytomy.adaptive_curriculum_review import (
    _REVIEW_POLICIES,
    CurriculumPublish,
    CurriculumRetire,
    Digest,
    _authorize_preview,
)
from src.services.studytomy.adaptive_diagnostic_selection import (
    SELECTOR_VERSION,
    DiagnosticLimits,
    DiagnosticManifest,
    DiagnosticPoolItem,
    DiagnosticRules,
)
from src.services.studytomy.adaptive_item_review import (
    _require_item_and_curriculum_approval,
)
from src.services.studytomy.adaptive_items import ConceptUUID
from src.services.studytomy.adaptive_mastery import PolicyUUID, resolve_mastery_policy
from src.services.studytomy.adaptive_objective_scoring import OBJECTIVE_EVALUATOR
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.errors import (
    POLICY_NOT_APPROVED,
    STALE_VERSION,
    studytomy_http_error,
)
from src.services.studytomy.idempotency import (
    canonical_request_hash,
    claim_idempotency,
    complete_idempotency,
)
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization

_POLICIES = (*_REVIEW_POLICIES, StudyPolicyKey.D_08)
_CREATE_OPERATION = "studytomy.adaptive.diagnostic.created"


class DiagnosticDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    curriculum_uuid: Annotated[
        str, StringConstraints(pattern=r"^curriculum_[0-9a-f-]{36}$")
    ]
    blueprint_uuid: (
        Annotated[str, StringConstraints(pattern=r"^blueprint_[0-9a-f-]{36}$")] | None
    ) = None
    name: Label
    revision: Version = 1
    required_concepts: Annotated[list[ConceptUUID], Field(min_length=1, max_length=100)]
    item_uuids: Annotated[
        list[Annotated[str, StringConstraints(pattern=r"^item_[0-9a-f-]{36}$")]],
        Field(min_length=1, max_length=200),
    ]
    selector_version: Literal[SELECTOR_VERSION]
    mastery_policy_uuid: PolicyUUID
    limits: DiagnosticLimits
    rules: DiagnosticRules


class DiagnosticStaffRead(BaseModel):
    uuid: str
    blueprint_uuid: str
    curriculum_uuid: str
    name: str
    revision: int
    version: int
    status: Literal["draft", "published", "retired"]
    selector_version: str
    mastery_policy_uuid: str
    manifest: DiagnosticManifest
    content_digest: Digest
    approved_at: datetime | None


class _DiagnosticApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_uuid: str
    digest: Digest
    evidence_reference: Reference


class _AssessmentAssistance(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    allow_help: Literal[False]
    reveal_modes: Annotated[
        list[Literal["never", "after_close"]], Field(min_length=1, max_length=2)
    ]


class _AssessmentAttempts(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    max_attempts: Annotated[int, Field(ge=1, le=20)]
    minimum_retake_wait_minutes: Annotated[int, Field(ge=0, le=43_200)]


def _policy_body(
    policies: list[StudyPolicyVersion], key: StudyPolicyKey
) -> StudyPolicyBody:
    return StudyPolicyBody.model_validate(
        next(policy.body for policy in policies if policy.policy_key == key.value)
    )


def _require_assessment_rules(policy: StudyPolicyBody, rules: DiagnosticRules) -> None:
    assistance_value = policy.specified_value("assistance")
    attempts_value = policy.specified_value("attempts")
    assistance = (
        assistance_value.get("adaptive_diagnostic")
        if isinstance(assistance_value, dict)
        else None
    )
    attempts = (
        attempts_value.get("adaptive_diagnostic")
        if isinstance(attempts_value, dict)
        else None
    )
    try:
        approved_assistance = _AssessmentAssistance.model_validate(assistance)
        approved_attempts = _AssessmentAttempts.model_validate(attempts)
    except ValidationError as error:
        raise studytomy_http_error(
            409,
            POLICY_NOT_APPROVED,
            "Approved diagnostic assessment rules are required.",
        ) from error
    if (
        rules.reveal not in approved_assistance.reveal_modes
        or rules.max_attempts > approved_attempts.max_attempts
        or rules.retake_wait_minutes < approved_attempts.minimum_retake_wait_minutes
    ):
        raise studytomy_http_error(
            409,
            POLICY_NOT_APPROVED,
            "The diagnostic rules exceed the approved assessment policy.",
        )


async def _manifest(
    db: AsyncSession,
    curriculum: StudyCurriculumRevision,
    policies: list[StudyPolicyVersion],
    concepts: tuple[str, ...],
    item_uuids: tuple[str, ...],
    limits: DiagnosticLimits,
    rules: DiagnosticRules,
    mastery_uuid: str,
) -> DiagnosticManifest:
    _require_assessment_rules(_policy_body(policies, StudyPolicyKey.D_08), rules)
    if len(concepts) != len(set(concepts)) or len(item_uuids) != len(set(item_uuids)):
        raise studytomy_http_error(
            422,
            "DIAGNOSTIC_POOL_DUPLICATE",
            "Required concepts and item revisions must be unique.",
        )
    if len(concepts) * limits.minimum_scored_per_concept > limits.total_items:
        raise studytomy_http_error(
            422,
            "DIAGNOSTIC_CAPACITY",
            "The overall item cap cannot cover the required concepts.",
        )
    concept_result = await db.execute(
        select(StudyConcept)
        .where(
            StudyConcept.org_id == curriculum.org_id,
            StudyConcept.curriculum_revision_id == curriculum.id,
            StudyConcept.archived.is_(False),
            StudyConcept.uuid.in_(concepts),
        )
        .with_for_update(read=True)
    )
    by_concept_id = {concept.id: concept.uuid for concept in concept_result.scalars()}
    if len(by_concept_id) != len(concepts):
        raise studytomy_http_error(
            422,
            "DIAGNOSTIC_CONCEPT_INVALID",
            "Use current concepts from this curriculum revision.",
        )
    item_result = await db.execute(
        select(StudyItemRevision)
        .where(
            StudyItemRevision.org_id == curriculum.org_id,
            StudyItemRevision.curriculum_revision_id == curriculum.id,
            StudyItemRevision.status == "published",
            StudyItemRevision.source_exposure == "private_original",
            StudyItemRevision.course_uuid == curriculum.owner_course_uuid,
            StudyItemRevision.evaluator_version == OBJECTIVE_EVALUATOR,
            StudyItemRevision.uuid.in_(item_uuids),
        )
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    )
    items = {item.uuid: item for item in item_result.scalars()}
    if len(items) != len(item_uuids):
        raise studytomy_http_error(
            422,
            "DIAGNOSTIC_ITEM_INVALID",
            "Use published independent items from this curriculum revision.",
        )
    approvals = _policy_body(policies, StudyPolicyKey.D_16).specified_value(
        "review_approvals"
    )
    entries = (
        approvals.get("adaptive_item_reviews") if isinstance(approvals, dict) else None
    )
    if not isinstance(entries, list):
        raise studytomy_http_error(
            409, POLICY_NOT_APPROVED, "The item pool lacks current review evidence."
        )
    references = {
        (entry["revision_uuid"], entry["digest"]): entry.get("evidence_reference")
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("revision_uuid"), str)
        and isinstance(entry.get("digest"), str)
        and entry.get("allowed_use") == "independent_diagnostic"
    }
    pool = []
    for uuid in item_uuids:
        item = items[uuid]
        if item.concept_id not in by_concept_id:
            raise studytomy_http_error(
                422,
                "DIAGNOSTIC_ITEM_INVALID",
                "Every item must map to a required concept.",
            )
        try:
            approval = CurriculumPublish(
                expected_version=item.version,
                expected_digest=item.content_digest,
                review_evidence=references.get((uuid, item.content_digest)),
            )
        except ValidationError as error:
            raise studytomy_http_error(
                409, POLICY_NOT_APPROVED, "The item pool lacks current review evidence."
            ) from error
        _require_item_and_curriculum_approval(policies, item, curriculum, approval)
        pool.append(
            DiagnosticPoolItem(
                uuid=uuid,
                concept_uuid=by_concept_id[item.concept_id],
                family_uuid=item.item_family_uuid,
                difficulty=item.difficulty,
                content_digest=item.content_digest,
            )
        )
    family_set = {item.family_uuid for item in pool}
    coverage = Counter(item.concept_uuid for item in pool)
    if len(family_set) != len(pool) or any(
        coverage[concept] < limits.minimum_scored_per_concept for concept in concepts
    ):
        raise studytomy_http_error(
            422,
            "DIAGNOSTIC_COVERAGE",
            "Each concept needs enough distinct reviewed item families.",
        )
    edges = (
        await db.execute(
            select(StudyConceptPrerequisite).where(
                StudyConceptPrerequisite.org_id == curriculum.org_id,
                StudyConceptPrerequisite.curriculum_revision_id == curriculum.id,
                StudyConceptPrerequisite.concept_id.in_(by_concept_id),
                StudyConceptPrerequisite.prerequisite_id.in_(by_concept_id),
            )
        )
    ).scalars()
    prerequisites: dict[str, list[str]] = {concept: [] for concept in concepts}
    for edge in edges:
        prerequisites[by_concept_id[edge.concept_id]].append(
            by_concept_id[edge.prerequisite_id]
        )
    return DiagnosticManifest(
        required_concepts=concepts,
        prerequisites={
            concept: tuple(sorted(parents, key=concepts.index))
            for concept, parents in prerequisites.items()
        },
        pool=tuple(pool),
        limits=limits,
        rules=rules,
        mastery_policy_digest=resolve_mastery_policy(
            _policy_body(policies, StudyPolicyKey.D_09), mastery_uuid
        ).digest,
    )


def _projection(
    row: StudyDiagnosticRevision, curriculum: StudyCurriculumRevision
) -> DiagnosticStaffRead:
    digest = row.content_digest or canonical_request_hash(
        {
            "schema": "adaptive_diagnostic_review_v1",
            "uuid": row.uuid,
            "blueprint_uuid": row.blueprint_uuid,
            "curriculum_uuid": curriculum.uuid,
            "curriculum_digest": curriculum.content_digest,
            "name": row.name,
            "revision": row.revision,
            "version": row.version,
            "selector_version": row.selector_version,
            "mastery_policy_uuid": row.mastery_policy_uuid,
            "manifest": row.manifest,
        }
    )
    return DiagnosticStaffRead(
        uuid=row.uuid,
        blueprint_uuid=row.blueprint_uuid,
        curriculum_uuid=curriculum.uuid,
        name=row.name,
        revision=row.revision,
        version=row.version,
        status=row.status,
        selector_version=row.selector_version,
        mastery_policy_uuid=row.mastery_policy_uuid,
        manifest=DiagnosticManifest.model_validate(row.manifest),
        content_digest=digest,
        approved_at=row.approved_at,
    )


async def create_diagnostic(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    payload: DiagnosticDraftCreate,
    idempotency_key: str,
) -> DiagnosticStaffRead:
    await lock_organization(db, org.id)
    curriculum = (
        await db.execute(
            select(StudyCurriculumRevision)
            .where(
                StudyCurriculumRevision.org_id == org.id,
                StudyCurriculumRevision.uuid == payload.curriculum_uuid,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if curriculum is None or not curriculum.owner_course_uuid:
        raise HTTPException(404, "Curriculum revision not found.")
    course = await authorize_adaptive_course_editor(
        request, db, org, actor, curriculum.owner_course_uuid
    )
    replay = await claim_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=_CREATE_OPERATION,
        idempotency_key=idempotency_key,
        request_payload=payload.model_dump(mode="json"),
    )
    if replay is not None:
        return DiagnosticStaffRead.model_validate(replay.response_projection)
    if curriculum.status != "published":
        raise studytomy_http_error(
            409, POLICY_NOT_APPROVED, "A published curriculum is required."
        )
    now = datetime.now(UTC)
    # Authoring a draft references existing item approvals. The exact blueprint
    # policy entry is only required when a reviewer publishes it.
    policies = await _current_policies(db, org, _POLICIES, now)
    manifest = await _manifest(
        db,
        curriculum,
        policies,
        tuple(payload.required_concepts),
        tuple(payload.item_uuids),
        payload.limits,
        payload.rules,
        payload.mastery_policy_uuid,
    )
    family = payload.blueprint_uuid or f"blueprint_{uuid4()}"
    previous = (
        await db.execute(
            select(StudyDiagnosticRevision)
            .where(
                StudyDiagnosticRevision.org_id == org.id,
                StudyDiagnosticRevision.blueprint_uuid == family,
            )
            .order_by(StudyDiagnosticRevision.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if previous is not None and previous.curriculum_revision_id != curriculum.id:
        raise HTTPException(404, "Diagnostic blueprint not found.")
    if payload.revision != (previous.revision if previous is not None else 0) + 1:
        raise studytomy_http_error(
            409, STALE_VERSION, "Use the next diagnostic blueprint revision."
        )
    row = StudyDiagnosticRevision(
        org_id=org.id,
        curriculum_revision_id=curriculum.id,
        blueprint_uuid=family,
        name=payload.name,
        revision=payload.revision,
        selector_version=payload.selector_version,
        mastery_policy_uuid=payload.mastery_policy_uuid,
        manifest=manifest.model_dump(mode="json"),
    )
    db.add(row)
    await db.flush()
    result = _projection(row, curriculum)
    await _record_command(
        db,
        org,
        actor,
        row,
        course.course_uuid,
        result,
        _CREATE_OPERATION,
        "DIAGNOSTIC_DRAFT_CREATED",
        idempotency_key,
        [],
    )
    return result


async def _locked_diagnostic(
    db: AsyncSession,
    org: Organization,
    uuid: str,
) -> tuple[StudyDiagnosticRevision, StudyCurriculumRevision]:
    await lock_organization(db, org.id)
    pair = (
        await db.execute(
            select(StudyDiagnosticRevision, StudyCurriculumRevision)
            .join(
                StudyCurriculumRevision,
                StudyCurriculumRevision.id
                == StudyDiagnosticRevision.curriculum_revision_id,
            )
            .where(
                StudyDiagnosticRevision.org_id == org.id,
                StudyDiagnosticRevision.uuid == uuid,
            )
            .with_for_update()
        )
    ).one_or_none()
    if pair is None or not pair[1].owner_course_uuid:
        raise HTTPException(404, "Diagnostic revision not found.")
    return tuple(pair)


async def read_diagnostic(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    uuid: str,
) -> DiagnosticStaffRead:
    row, curriculum = await _locked_diagnostic(db, org, uuid)
    await _authorize_preview(request, db, org, actor, curriculum.owner_course_uuid)
    return _projection(row, curriculum)


async def review_diagnostic(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    uuid: str,
    command: CurriculumPublish | CurriculumRetire,
    idempotency_key: str,
) -> DiagnosticStaffRead:
    row, curriculum = await _locked_diagnostic(db, org, uuid)
    now = datetime.now(UTC)
    await authorize_studytomy_staff_action(
        db,
        actor,
        org,
        StudytomyCapabilityName.CONTENT_RELEASE_REVIEW,
        target_scope_kind="course",
        target_resource_uuid=curriculum.owner_course_uuid,
        hide_existence_on_denial=True,
        now=now,
    )
    policies = await _current_policies(db, org, _POLICIES, now)
    publishing = isinstance(command, CurriculumPublish)
    operation = (
        f"studytomy.adaptive.diagnostic.{'published' if publishing else 'retired'}"
    )
    replay = await claim_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_payload={"uuid": uuid, "command": command.model_dump(mode="json")},
    )
    if replay is not None:
        if isinstance(command, CurriculumPublish):
            _require_exact_approval(policies, row, command)
        return DiagnosticStaffRead.model_validate(replay.response_projection)
    if row.version != command.expected_version or row.status != (
        "draft" if publishing else "published"
    ):
        raise studytomy_http_error(
            409, STALE_VERSION, "Reload the current diagnostic revision."
        )
    if isinstance(command, CurriculumPublish):
        if curriculum.status != "published" or row.selector_version != SELECTOR_VERSION:
            raise studytomy_http_error(
                409,
                POLICY_NOT_APPROVED,
                "The diagnostic requires an approved curriculum and selector.",
            )
        frozen = _projection(row, curriculum)
        if frozen.content_digest != command.expected_digest:
            raise studytomy_http_error(
                409, STALE_VERSION, "The reviewed diagnostic digest has changed."
            )
        _require_exact_approval(policies, row, command)
        owner = (
            (
                await db.execute(
                    select(Course.id)
                    .where(
                        Course.org_id == org.id,
                        Course.course_uuid == curriculum.owner_course_uuid,
                    )
                    .limit(2)
                    .with_for_update(read=True)
                )
            )
            .scalars()
            .all()
        )
        if len(owner) != 1:
            raise studytomy_http_error(
                409, STALE_VERSION, "The diagnostic's owning course is unavailable."
            )
        manifest = frozen.manifest
        current = await _manifest(
            db,
            curriculum,
            policies,
            manifest.required_concepts,
            tuple(item.uuid for item in manifest.pool),
            manifest.limits,
            manifest.rules,
            row.mastery_policy_uuid,
        )
        if current != manifest:
            raise studytomy_http_error(
                409,
                STALE_VERSION,
                "The reviewed item pool or mastery policy has changed.",
            )
        row.status = "published"
        row.content_digest = frozen.content_digest
        row.reviewer_id = actor.id
        row.approved_at = now
        row.policy_version_ids = [int(policy.id) for policy in policies]
        reason = "DIAGNOSTIC_REVIEW_PASSED"
    else:
        row.status = "retired"
        reason = command.reason
    row.version += 1
    row.updated_at = now
    await db.flush()
    result = _projection(row, curriculum)
    await _record_command(
        db,
        org,
        actor,
        row,
        curriculum.owner_course_uuid,
        result,
        operation,
        reason,
        idempotency_key,
        [int(policy.id) for policy in policies],
    )
    return result


def _require_exact_approval(
    policies: list[StudyPolicyVersion],
    row: StudyDiagnosticRevision,
    command: CurriculumPublish,
) -> None:
    approvals = _policy_body(policies, StudyPolicyKey.D_16).specified_value(
        "review_approvals"
    )
    entries = (
        approvals.get("adaptive_diagnostic_reviews")
        if isinstance(approvals, dict)
        else None
    )
    expected = _DiagnosticApproval(
        revision_uuid=row.uuid,
        digest=command.expected_digest,
        evidence_reference=command.review_evidence,
    )
    try:
        count = sum(
            _DiagnosticApproval.model_validate(entry) == expected
            for entry in (entries if isinstance(entries, list) else ())
        )
    except ValidationError:
        count = 0
    if count != 1:
        raise studytomy_http_error(
            409,
            POLICY_NOT_APPROVED,
            "Current policy must approve the exact diagnostic blueprint.",
        )


async def _record_command(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    row: StudyDiagnosticRevision,
    course_uuid: str,
    result: DiagnosticStaffRead,
    operation: str,
    reason: str,
    key: str,
    policy_ids: list[int],
) -> None:
    await append_business_event(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        action=operation,
        reason_code=reason,
        subject_uuid=course_uuid,
        resource_kind="diagnostic_revision",
        resource_uuid=row.uuid,
        after_state={"status": row.status, "version": row.version},
        policy_refs=policy_ids,
    )
    await complete_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=operation,
        idempotency_key=key,
        resource_kind="diagnostic_revision",
        resource_uuid=row.uuid,
        response_status=201 if operation == _CREATE_OPERATION else 200,
        response_projection=result.model_dump(mode="json"),
    )
    await db.commit()
