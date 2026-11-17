"""Review and freeze exact item snapshots for practice."""

from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.adaptive import StudyConcept, StudyCurriculumRevision
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
from src.services.studytomy.adaptive_curriculum import Reference
from src.services.studytomy.adaptive_curriculum_review import (
    _REVIEW_POLICIES,
    CurriculumPublish,
    CurriculumRetire,
    Digest,
    _authorize_preview,
    _require_exact_policy_approval,
)
from src.services.studytomy.adaptive_item_source import (
    _load_native_item_source,
    _project_native_item_source,
)
from src.services.studytomy.adaptive_items import ItemRevisionStaffRead, _project
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

ItemAllowedUse = Literal["practice_only", "independent_diagnostic"]


def _allowed_use(row: StudyItemRevision) -> ItemAllowedUse:
    return (
        "independent_diagnostic"
        if row.source_exposure == "private_original"
        else "practice_only"
    )


class ItemReviewSnapshot(BaseModel):
    item: ItemRevisionStaffRead
    content_digest: Digest
    rights_reference: str
    allowed_use: ItemAllowedUse


class ItemReviewedRead(ItemRevisionStaffRead):
    status: Literal["published", "retired"]
    content_digest: Digest
    approved_at: datetime
    allowed_use: ItemAllowedUse


class _ItemApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_uuid: str
    digest: Digest
    rights_reference: Reference
    evidence_reference: Reference
    allowed_use: ItemAllowedUse
    exposure_evidence_reference: Reference | None = None


async def _locked_item(
    db: AsyncSession,
    org: Organization,
    item_uuid: str,
) -> tuple[StudyItemRevision, StudyConcept, StudyCurriculumRevision]:
    await lock_organization(db, org.id)
    result = await db.execute(
        select(StudyItemRevision, StudyConcept, StudyCurriculumRevision)
        .join(StudyConcept, StudyConcept.id == StudyItemRevision.concept_id)
        .join(
            StudyCurriculumRevision,
            StudyCurriculumRevision.id == StudyItemRevision.curriculum_revision_id,
        )
        .where(StudyItemRevision.org_id == org.id, StudyItemRevision.uuid == item_uuid)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    row = result.one_or_none()
    if row is None or not row[2].owner_course_uuid:
        raise HTTPException(404, "Item revision not found.")
    return tuple(row)


def _snapshot(
    row: StudyItemRevision, concept: StudyConcept, curriculum: StudyCurriculumRevision
) -> ItemReviewSnapshot:
    item = _project(row, concept.uuid)
    allowed_use = _allowed_use(row)
    digest = canonical_request_hash(
        {
            "schema": "adaptive_item_review_v1",
            "item": item.model_dump(mode="json"),
            "curriculum_uuid": curriculum.uuid,
            "rights_reference": row.rights_reference,
            "assignment_uuid": row.assignment_uuid,
            "source_course_uuid": row.course_uuid,
            "owner_course_uuid": curriculum.owner_course_uuid,
            "evaluator_version": row.evaluator_version,
            "private_answer": row.private_answer,
            "allowed_use": allowed_use,
        }
    )
    return ItemReviewSnapshot(
        item=item,
        content_digest=digest,
        rights_reference=row.rights_reference,
        allowed_use=allowed_use,
    )


async def item_review_snapshot(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    item_uuid: str,
) -> ItemReviewSnapshot:
    row, concept, curriculum = await _locked_item(db, org, item_uuid)
    for course_uuid in sorted({row.course_uuid, curriculum.owner_course_uuid}):
        await _authorize_preview(request, db, org, actor, course_uuid)
    if row.status != "draft":
        raise studytomy_http_error(
            409, STALE_VERSION, "Only draft item revisions can be reviewed."
        )
    return _snapshot(row, concept, curriculum)


def _require_item_and_curriculum_approval(
    policies: list[StudyPolicyVersion],
    row: StudyItemRevision,
    curriculum: StudyCurriculumRevision,
    command: CurriculumPublish,
) -> None:
    d16 = next(
        policy for policy in policies if policy.policy_key == StudyPolicyKey.D_16.value
    )
    body = StudyPolicyBody.model_validate(d16.body)
    approvals = body.specified_value("review_approvals")
    allowed_use = _allowed_use(row)
    expected = _ItemApproval(
        revision_uuid=row.uuid,
        digest=command.expected_digest,
        rights_reference=row.rights_reference,
        evidence_reference=command.review_evidence,
        allowed_use=allowed_use,
    )
    rights = body.specified_value("content_rights")
    original_rights = (
        rights.get("adaptive_original_item_rights")
        if isinstance(rights, dict)
        else None
    )
    acceptable_rights = (
        isinstance(original_rights, list) and row.rights_reference in original_rights
        if allowed_use == "independent_diagnostic"
        else row.rights_reference == curriculum.rights_reference
    )
    if isinstance(approvals, dict) and acceptable_rights:
        item_entries = approvals.get("adaptive_item_reviews")
        curriculum_entries = approvals.get("adaptive_curriculum_reviews")
        try:
            matching_items = [
                entry
                for entry in (
                    _ItemApproval.model_validate(raw)
                    for raw in (item_entries if isinstance(item_entries, list) else ())
                )
                if entry.model_copy(update={"exposure_evidence_reference": None})
                == expected
            ]
        except ValidationError:
            matching_items = []
        if (
            len(matching_items) == 1
            and isinstance(curriculum_entries, list)
            and (
                allowed_use != "independent_diagnostic"
                or matching_items[0].exposure_evidence_reference
            )
        ):
            for entry in curriculum_entries:
                if (
                    not isinstance(entry, dict)
                    or entry.get("revision_uuid") != curriculum.uuid
                    or entry.get("digest") != curriculum.content_digest
                    or entry.get("rights_reference") != curriculum.rights_reference
                ):
                    continue
                try:
                    parent_approval = CurriculumPublish(
                        expected_version=curriculum.version,
                        expected_digest=curriculum.content_digest,
                        review_evidence=entry.get("evidence_reference"),
                    )
                except ValidationError:
                    continue
                _require_exact_policy_approval(policies, curriculum, parent_approval)
                return
    raise studytomy_http_error(
        409,
        POLICY_NOT_APPROVED,
        "Current policies must approve this exact item, its intended use and its curriculum.",
    )


async def _require_current_native_source(
    db: AsyncSession,
    org: Organization,
    row: StudyItemRevision,
) -> str:
    try:
        task, assignment, course, activity = await _load_native_item_source(
            db, org, row.source_task_uuid, lock=True
        )
        if (
            course.course_uuid == row.course_uuid
            and assignment.assignment_uuid == row.assignment_uuid
        ):
            source, answer = _project_native_item_source(
                task,
                assignment,
                course,
                activity,
                row.source_question_uuid,
            )
            if (
                source.source_digest == row.source_digest
                and answer == row.private_answer
            ):
                return activity.activity_uuid
    except HTTPException as error:
        if error.status_code not in {404, 422}:
            raise
    raise studytomy_http_error(
        409, STALE_VERSION, "The native question changed. Create a new item revision."
    )


async def review_item(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    item_uuid: str,
    command: CurriculumPublish | CurriculumRetire,
    idempotency_key: str,
) -> ItemReviewedRead:
    row, concept, curriculum = await _locked_item(db, org, item_uuid)
    now = datetime.now(UTC)
    for course_uuid in sorted({row.course_uuid, curriculum.owner_course_uuid}):
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
    policies = await _current_policies(
        db, org, (*_REVIEW_POLICIES, StudyPolicyKey.D_08), now
    )
    policy_ids = [int(policy.id) for policy in policies]
    publishing = isinstance(command, CurriculumPublish)
    operation = f"studytomy.adaptive.item.{'published' if publishing else 'retired'}"
    replay = await claim_idempotency(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation=operation,
        idempotency_key=idempotency_key,
        request_payload={
            "item_uuid": item_uuid,
            "command": command.model_dump(mode="json"),
        },
    )
    if replay is not None:
        if isinstance(command, CurriculumPublish):
            _require_item_and_curriculum_approval(policies, row, curriculum, command)
        return ItemReviewedRead.model_validate(replay.response_projection)

    if row.version != command.expected_version or row.status != (
        "draft" if publishing else "published"
    ):
        raise studytomy_http_error(
            409, STALE_VERSION, "Reload the current item revision."
        )
    before = {"status": row.status, "version": row.version}
    if isinstance(command, CurriculumPublish):
        if curriculum.status != "published" or concept.archived:
            raise studytomy_http_error(
                409,
                POLICY_NOT_APPROVED,
                "The item requires a current published curriculum concept.",
            )
        snapshot = _snapshot(row, concept, curriculum)
        if snapshot.content_digest != command.expected_digest:
            raise studytomy_http_error(
                409, STALE_VERSION, "The reviewed item digest has changed."
            )
        _require_item_and_curriculum_approval(policies, row, curriculum, command)
        if row.source_exposure != "private_original":
            await _require_current_native_source(db, org, row)
        row.status = "published"
        row.content_digest = snapshot.content_digest
        row.approved_at = now
        row.reviewer_id = actor.id
        row.policy_version_ids = policy_ids
        reason = (
            "ITEM_DIAGNOSTIC_REVIEW_PASSED"
            if row.source_exposure == "private_original"
            else "ITEM_PRACTICE_REVIEW_PASSED"
        )
    else:
        row.status = "retired"
        reason = command.reason
    row.version += 1
    row.updated_at = now
    await db.flush()
    result = ItemReviewedRead(
        **_project(row, concept.uuid).model_dump(),
        content_digest=row.content_digest,
        approved_at=row.approved_at,
        allowed_use=_allowed_use(row),
    )
    await append_business_event(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        operation_uuid=None,
        action=operation,
        reason_code=reason,
        resource_kind="item_revision",
        resource_uuid=row.uuid,
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
        resource_kind="item_revision",
        resource_uuid=row.uuid,
        response_status=200,
        response_projection=result.model_dump(mode="json"),
    )
    await db.commit()
    return result
