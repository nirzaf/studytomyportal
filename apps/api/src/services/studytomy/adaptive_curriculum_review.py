"""Exact-version review, publication and retirement of curriculum revisions."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.activities import Activity
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.adaptive import (
    CurriculumDraftRead,
    CurriculumReviewedRead,
    StudyConcept,
    StudyConceptActivity,
    StudyConceptPrerequisite,
    StudyCurriculumRevision,
)
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
from src.services.studytomy.adaptive_curriculum import (
    ConceptRead,
    CurriculumGraphUpdate,
    PrerequisiteDraft,
    Reference,
    Version,
    _validate_graph,
    curriculum_activity_digest,
)
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

_REVIEW_POLICIES = (
    StudyPolicyKey.D_02,
    StudyPolicyKey.D_09,
    StudyPolicyKey.D_10,
    StudyPolicyKey.D_15,
    StudyPolicyKey.D_16,
)
Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class CurriculumPublish(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Version
    expected_digest: Digest
    review_evidence: Reference


class CurriculumRetire(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Version
    reason: Literal["SUPERSEDED", "CONTENT_WITHDRAWN", "REVIEW_REQUIRED"]


class CurriculumMappingSnapshot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    concept_uuid: str
    course_uuid: str
    activity_uuid: str
    activity_version: int
    activity_digest: Digest
    usage: str
    archived: bool
    review_reference: str | None


class CurriculumConceptSnapshot(ConceptRead):
    archived: bool


class CurriculumReviewSnapshot(BaseModel):
    curriculum: CurriculumDraftRead
    source_reference: str
    rights_reference: str
    concepts: list[CurriculumConceptSnapshot]
    prerequisites: list[PrerequisiteDraft]
    mappings: list[CurriculumMappingSnapshot]
    content_digest: Digest


class ReviewedCurriculumObjectives(BaseModel):
    curriculum: CurriculumReviewedRead
    concepts: list[CurriculumConceptSnapshot]
    prerequisites: list[PrerequisiteDraft]


class ReviewedCurriculumPage(BaseModel):
    revisions: list[CurriculumReviewedRead]
    next_cursor: str | None


class _CurriculumApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_uuid: str
    digest: Digest
    rights_reference: Reference
    evidence_reference: Reference


async def _locked_revision(
    db: AsyncSession,
    org: Organization,
    revision_uuid: str,
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
    if row is None or not row.owner_course_uuid:
        raise HTTPException(404, "Curriculum revision not found.")
    return row


async def _mappings(
    db: AsyncSession, row: StudyCurriculumRevision
) -> list[StudyConceptActivity]:
    return list(
        (
            await db.execute(
                select(StudyConceptActivity).where(
                    StudyConceptActivity.org_id == row.org_id,
                    StudyConceptActivity.curriculum_revision_id == row.id,
                )
            )
        ).scalars()
    )


async def _snapshot(
    db: AsyncSession,
    row: StudyCurriculumRevision,
    mappings: list[StudyConceptActivity],
    *,
    draft_projection: CurriculumDraftRead | None = None,
    allow_empty_draft: bool = False,
) -> CurriculumReviewSnapshot:
    concepts = list(
        (
            await db.execute(
                select(StudyConcept)
                .where(
                    StudyConcept.org_id == row.org_id,
                    StudyConcept.curriculum_revision_id == row.id,
                )
                .order_by(StudyConcept.code)
            )
        ).scalars()
    )
    if not concepts and not allow_empty_draft:
        raise studytomy_http_error(
            422, "CURRICULUM_GRAPH_EMPTY", "Add concepts before review."
        )
    by_id = {concept.id: concept for concept in concepts}
    edges = (
        await db.execute(
            select(StudyConceptPrerequisite).where(
                StudyConceptPrerequisite.org_id == row.org_id,
                StudyConceptPrerequisite.curriculum_revision_id == row.id,
            )
        )
    ).scalars()
    prerequisites = sorted(
        [
            PrerequisiteDraft(
                concept_code=by_id[edge.concept_id].code,
                prerequisite_code=by_id[edge.prerequisite_id].code,
            )
            for edge in edges
        ],
        key=lambda edge: (edge.concept_code, edge.prerequisite_code),
    )
    projections = [
        CurriculumConceptSnapshot.model_validate(concept) for concept in concepts
    ]
    if projections:
        _validate_graph(
            CurriculumGraphUpdate(
                expected_version=row.version,
                concepts=projections,
                prerequisites=prerequisites,
            )
        )
    projection = draft_projection or CurriculumDraftRead.model_validate(row)
    manifest = {
        "curriculum": projection.model_dump(mode="json"),
        "source_reference": row.source_reference,
        "rights_reference": row.rights_reference,
        "concepts": [concept.model_dump(mode="json") for concept in projections],
        "prerequisites": [edge.model_dump(mode="json") for edge in prerequisites],
        "mappings": sorted(
            [
                {
                    "concept_uuid": by_id[mapping.concept_id].uuid,
                    "course_uuid": mapping.course_uuid,
                    "activity_uuid": mapping.activity_uuid,
                    "activity_version": mapping.activity_version,
                    "activity_digest": mapping.activity_digest,
                    "usage": mapping.usage,
                    "archived": mapping.archived,
                    "review_reference": mapping.review_reference,
                }
                for mapping in mappings
            ],
            key=lambda mapping: (
                mapping["concept_uuid"],
                mapping["course_uuid"],
                mapping["activity_uuid"],
                mapping["usage"],
            ),
        ),
    }
    return CurriculumReviewSnapshot(
        **manifest,
        content_digest=canonical_request_hash(
            {"schema": "adaptive_curriculum_review_v1", **manifest}
        ),
    )


async def curriculum_review_snapshot(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    revision_uuid: str,
) -> CurriculumReviewSnapshot:
    row = await _locked_revision(db, org, revision_uuid)
    await _authorize_preview(request, db, org, actor, row.owner_course_uuid)
    if row.status != "draft":
        raise studytomy_http_error(
            409, STALE_VERSION, "Only draft revisions can be reviewed."
        )
    mappings = await _mappings(db, row)
    for course_uuid in sorted(
        {mapping.course_uuid for mapping in mappings} - {row.owner_course_uuid}
    ):
        await _authorize_preview(request, db, org, actor, course_uuid)
    return await _snapshot(db, row, mappings, allow_empty_draft=True)


async def read_reviewed_curriculum_objectives(
    request: Request, db: AsyncSession, org: Organization, actor: PublicUser, revision_uuid: str,
) -> ReviewedCurriculumObjectives:
    row = await _locked_revision(db, org, revision_uuid)
    await _authorize_preview(request, db, org, actor, row.owner_course_uuid)
    if row.status not in {"published", "retired"}:
        raise HTTPException(404, "Reviewed curriculum revision not found.")
    # Publish and retire each advance the record version without changing the reviewed graph.
    reviewed_version = row.version - (2 if row.status == "retired" else 1)
    reviewed = CurriculumReviewedRead.model_validate(row)
    draft_projection = CurriculumDraftRead.model_validate({
        **reviewed.model_dump(exclude={"approved_at", "content_digest"}),
        "status": "draft", "version": reviewed_version,
    })
    snapshot = await _snapshot(db, row, await _mappings(db, row), draft_projection=draft_projection)
    if snapshot.content_digest != row.content_digest:
        raise studytomy_http_error(409, "CURRICULUM_EVIDENCE_CHANGED", "Stored objectives no longer match the reviewed revision.")
    return ReviewedCurriculumObjectives(
        curriculum=reviewed,
        concepts=snapshot.concepts, prerequisites=snapshot.prerequisites,
    )


async def list_reviewed_course_curricula(
    request: Request, db: AsyncSession, org: Organization, actor: PublicUser,
    course_uuid: str, *, after: str | None, limit: int,
) -> ReviewedCurriculumPage:
    exists = await db.scalar(select(Course.id).where(Course.org_id == org.id, Course.course_uuid == course_uuid))
    if exists is None:
        raise HTTPException(404, "Course not found.")
    await _authorize_preview(request, db, org, actor, course_uuid)
    revision = StudyCurriculumRevision
    statement = select(revision).where(
        revision.org_id == org.id, revision.owner_course_uuid == course_uuid,
        revision.status.in_(("published", "retired")),
    )
    if after is not None:
        statement = statement.where(revision.uuid > after)
    rows = (await db.execute(statement.order_by(revision.uuid).limit(limit + 1))).scalars().all()
    return ReviewedCurriculumPage(
        revisions=[CurriculumReviewedRead.model_validate(row) for row in rows[:limit]],
        next_cursor=rows[limit - 1].uuid if len(rows) > limit else None,
    )


async def _authorize_preview(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    course_uuid: str,
) -> None:
    try:
        await authorize_adaptive_course_editor(request, db, org, actor, course_uuid)
    except HTTPException as error:
        if error.status_code not in {403, 404}:
            raise
        await authorize_studytomy_staff_action(
            db,
            actor,
            org,
            StudytomyCapabilityName.CONTENT_RELEASE_REVIEW,
            target_scope_kind="course",
            target_resource_uuid=course_uuid,
            hide_existence_on_denial=True,
        )


async def _require_current_sources(
    db: AsyncSession,
    org: Organization,
    mappings: list[StudyConceptActivity],
) -> None:
    active = [mapping for mapping in mappings if not mapping.archived]
    if not active:
        return
    pairs = (
        await db.execute(
            select(Activity, Course)
            .join(Course, Activity.course_id == Course.id)
            .where(
                Activity.org_id == org.id,
                Course.org_id == org.id,
                Activity.id.in_({mapping.activity_id for mapping in active}),
            )
            .order_by(Activity.id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
    ).all()
    by_id = {activity.id: (activity, course) for activity, course in pairs}
    for mapping in active:
        source = by_id.get(mapping.activity_id)
        if source is not None:
            activity, course = source
            if (
                course.id == mapping.course_id
                and course.course_uuid == mapping.course_uuid
                and activity.activity_uuid == mapping.activity_uuid
                and activity.current_version == mapping.activity_version
                and curriculum_activity_digest(activity) == mapping.activity_digest
            ):
                continue
        raise studytomy_http_error(
            409, STALE_VERSION, "A mapped lesson changed. Review its current revision."
        )


def _require_exact_policy_approval(
    policies: list[StudyPolicyVersion],
    row: StudyCurriculumRevision,
    command: CurriculumPublish,
) -> None:
    bodies = {p.policy_key: StudyPolicyBody.model_validate(p.body) for p in policies}
    curriculum = bodies["D-02"]
    claims = curriculum.specified_value("curriculum_claims")
    rights = bodies["D-16"].specified_value("content_rights")
    reviews = bodies["D-16"].specified_value("review_approvals")
    expected = _CurriculumApproval(
        revision_uuid=row.uuid,
        digest=command.expected_digest,
        rights_reference=row.rights_reference,
        evidence_reference=command.review_evidence,
    )
    approved_scope = [
        (curriculum.specified_value(area), value)
        for area, value in (
            ("subjects", row.subject),
            ("learner_levels", row.level),
            ("languages", row.language),
        )
    ]
    if (
        all(
            isinstance(values, list) and value in values
            for values, value in approved_scope
        )
        and isinstance(claims, dict)
        and isinstance(claims.get("adaptive_curriculum_keys"), list)
        and row.curriculum_key in claims["adaptive_curriculum_keys"]
        and isinstance(rights, dict)
        and isinstance(rights.get("adaptive_curriculum_rights"), list)
        and row.rights_reference in rights["adaptive_curriculum_rights"]
        and isinstance(reviews, dict)
        and isinstance(reviews.get("adaptive_curriculum_reviews"), list)
    ):
        try:
            matches = sum(
                _CurriculumApproval.model_validate(entry) == expected
                for entry in reviews["adaptive_curriculum_reviews"]
            )
        except ValidationError:
            matches = 0
        if matches == 1:
            return
    raise studytomy_http_error(
        409,
        POLICY_NOT_APPROVED,
        "Current policies must approve this exact curriculum and its rights evidence.",
    )


async def review_curriculum(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    revision_uuid: str,
    command: CurriculumPublish | CurriculumRetire,
    idempotency_key: str,
) -> CurriculumReviewedRead:
    publishing = isinstance(command, CurriculumPublish)
    operation = (
        f"studytomy.adaptive.curriculum.{'published' if publishing else 'retired'}"
    )
    row = await _locked_revision(db, org, revision_uuid)
    mappings = await _mappings(db, row)
    now = datetime.now(UTC)
    for course_uuid in sorted(
        {row.owner_course_uuid} | {mapping.course_uuid for mapping in mappings}
    ):
        await authorize_studytomy_staff_action(
            db,
            actor,
            org,
            StudytomyCapabilityName.CONTENT_RELEASE_REVIEW,
            target_scope_kind="course",
            target_resource_uuid=course_uuid,
            hide_existence_on_denial=True,
            now=now,
        )
    policies = await _current_policies(db, org, _REVIEW_POLICIES, now)
    policy_ids = [int(policy.id) for policy in policies]
    replay = await claim_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_payload={
            "revision_uuid": revision_uuid,
            "command": command.model_dump(mode="json"),
        },
    )
    if replay is not None:
        if isinstance(command, CurriculumPublish):
            _require_exact_policy_approval(policies, row, command)
        return CurriculumReviewedRead.model_validate(replay.response_projection)
    if row.version != command.expected_version or row.status != (
        "draft" if publishing else "published"
    ):
        raise studytomy_http_error(
            409, STALE_VERSION, "Reload the current curriculum revision."
        )

    before = {"status": row.status, "version": row.version}
    if isinstance(command, CurriculumPublish):
        owner = (
            (
                await db.execute(
                    select(Course.id)
                    .where(
                        Course.org_id == org.id,
                        Course.course_uuid == row.owner_course_uuid,
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
                409, STALE_VERSION, "The curriculum's owning course is unavailable."
            )
        snapshot = await _snapshot(db, row, mappings)
        if snapshot.content_digest != command.expected_digest:
            raise studytomy_http_error(
                409, STALE_VERSION, "The reviewed curriculum digest has changed."
            )
        _require_exact_policy_approval(policies, row, command)
        await _require_current_sources(db, org, mappings)
        row.status = "published"
        row.reviewer_id = actor.id
        row.approved_at = now
        row.content_digest = snapshot.content_digest
        row.policy_version_ids = policy_ids
        reason = "CURRICULUM_REVIEW_PASSED"
    else:
        row.status = "retired"
        reason = command.reason
    row.updated_at = now
    row.version += 1
    await db.flush()
    result = CurriculumReviewedRead.model_validate(row)
    await append_business_event(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        subject_uuid=row.owner_course_uuid,
        resource_kind="curriculum",
        resource_uuid=row.uuid,
        action=operation,
        reason_code=reason,
        before_state=before,
        after_state={"status": row.status, "version": row.version},
        policy_refs=policy_ids,
    )
    await complete_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=operation,
        idempotency_key=idempotency_key,
        resource_kind="curriculum",
        resource_uuid=row.uuid,
        response_status=200,
        response_projection=result.model_dump(mode="json"),
    )
    await db.commit()
    return result
