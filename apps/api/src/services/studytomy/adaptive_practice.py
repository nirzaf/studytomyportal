"""Resolve a supported practice item under current review and lesson access."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.assignments import Assignment
from src.db.organizations import Organization
from src.db.studytomy.adaptive import StudyConcept, StudyCurriculumRevision
from src.db.studytomy.adaptive_items import StudyItemRevision
from src.db.studytomy.governance import StudyPolicyKey
from src.db.users import PublicUser
from src.services.studytomy.access_policy import resolve_managed_release_authority
from src.services.studytomy.adaptive_attempt_access import (
    _ATTEMPT_POLICIES,
    ResolvedManagedReleaseSource,
    require_mapped_release_source,
    require_mapped_source,
)
from src.services.studytomy.adaptive_curriculum_review import CurriculumPublish
from src.services.studytomy.adaptive_diagnostics import _policy_body
from src.services.studytomy.adaptive_item_review import (
    _require_current_native_source,
    _require_item_and_curriculum_approval,
)
from src.services.studytomy.adaptive_item_source import retained_native_item_source
from src.services.studytomy.adaptive_mastery import (
    MasteryPolicy,
    PolicyUUID,
    resolve_mastery_policy,
)
from src.services.studytomy.errors import (
    POLICY_NOT_APPROVED,
    STALE_VERSION,
    studytomy_http_error,
)
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.learners import _current_policies


class PracticeRules(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    mode: Literal["supported"]
    expires_after_minutes: Annotated[int, Field(ge=1, le=1440)]
    mastery_policy_uuid: PolicyUUID


@dataclass(frozen=True, slots=True)
class PracticeAccess:
    curriculum: StudyCurriculumRevision
    item: StudyItemRevision
    rules: PracticeRules
    policy: MasteryPolicy
    policy_ids: list[int]
    activity_uuid: str
    release_source: ResolvedManagedReleaseSource | None = None

    @property
    def available(self) -> bool:
        return self.curriculum.status == "published" and self.item.status == "published"

    @property
    def manifest_digest(self) -> str:
        projection = {
            "item_uuid": self.item.uuid,
            "item_digest": self.item.content_digest,
            "curriculum_digest": self.curriculum.content_digest,
            "activity_uuid": self.activity_uuid,
            "rules": self.rules.model_dump(mode="json"),
            "mastery_digest": self.policy.digest,
        }
        if self.release_source is not None:
            projection["source_binding_digest"] = (
                self.release_source.source_binding_digest
            )
        return canonical_request_hash(projection)


async def require_practice_access(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    activity_uuid: str,
    now: datetime,
    *,
    concept_uuid: str | None = None,
    item_id: int | None = None,
    course_uuid: str | None = None,
    course_release_uuid: str | None = None,
) -> PracticeAccess:
    release_authority = None
    if course_release_uuid is not None:
        if not course_uuid:
            raise HTTPException(404, "Practice source not found.")
        release_authority = await resolve_managed_release_authority(
            db,
            org,
            actor,
            course_uuid,
            course_release_uuid,
            now=now,
        )
    query = (
        select(StudyConcept, StudyCurriculumRevision)
        .join(
            StudyCurriculumRevision,
            StudyCurriculumRevision.id == StudyConcept.curriculum_revision_id,
        )
        .where(
            StudyConcept.org_id == org.id,
            StudyCurriculumRevision.org_id == org.id,
            StudyConcept.archived.is_(False),
            StudyCurriculumRevision.status.in_(
                ("published", "retired") if item_id is not None else ("published",)
            ),
        )
    )
    if item_id is None:
        query = query.where(StudyConcept.uuid == concept_uuid)
    else:
        query = query.join(
            StudyItemRevision, StudyItemRevision.concept_id == StudyConcept.id
        ).where(
            StudyItemRevision.org_id == org.id,
            StudyItemRevision.id == item_id,
        )
    row = (
        await db.execute(
            query.with_for_update(read=True, of=(StudyConcept, StudyCurriculumRevision))
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(404, "Practice source not found.")
    concept, curriculum = row
    release_source = None
    if course_release_uuid is None:
        activity = await require_mapped_source(
            request,
            db,
            org,
            actor,
            curriculum,
            (concept.uuid,),
            activity_uuid,
            for_practice=True,
        )
        activity_id = activity.id
    else:
        if (
            release_authority is None
            or curriculum.owner_course_uuid != course_uuid
            or curriculum.org_id != org.id
        ):
            raise HTTPException(404, "Practice source not found.")
        release_source = await require_mapped_release_source(
            db,
            org,
            actor,
            release_authority.profile,
            curriculum,
            (concept.uuid,),
            course_uuid,
            course_release_uuid,
            activity_uuid,
            now,
            for_practice=True,
        )
    policies = await _current_policies(db, org, _ATTEMPT_POLICIES, now)
    d08 = _policy_body(policies, StudyPolicyKey.D_08).specified_value("assistance")
    try:
        rules = PracticeRules.model_validate(
            d08.get("adaptive_practice") if isinstance(d08, dict) else None
        )
    except ValidationError as error:
        raise studytomy_http_error(
            409, POLICY_NOT_APPROVED, "Current supported practice rules are required."
        ) from error
    policy = resolve_mastery_policy(
        _policy_body(policies, StudyPolicyKey.D_09), rules.mastery_policy_uuid
    )
    d16 = _policy_body(policies, StudyPolicyKey.D_16).specified_value(
        "review_approvals"
    )
    entries = d16.get("adaptive_item_reviews") if isinstance(d16, dict) else None
    approved = (
        {
            (entry["revision_uuid"], entry["digest"]): entry.get("evidence_reference")
            for entry in entries
            if isinstance(entry, dict)
            and entry.get("allowed_use") == "practice_only"
            and isinstance(entry.get("revision_uuid"), str)
            and isinstance(entry.get("digest"), str)
        }
        if isinstance(entries, list)
        else {}
    )
    items = (
        select(StudyItemRevision)
        .where(
            StudyItemRevision.org_id == org.id,
            StudyItemRevision.curriculum_revision_id == curriculum.id,
            StudyItemRevision.concept_id == concept.id,
            StudyItemRevision.course_uuid == curriculum.owner_course_uuid,
            StudyItemRevision.assignment_uuid.is_not(None),
        )
        .with_for_update(read=True, of=StudyItemRevision)
        .execution_options(populate_existing=True)
    )
    if release_source is None:
        items = items.join(
            Assignment, Assignment.assignment_uuid == StudyItemRevision.assignment_uuid
        ).where(Assignment.org_id == org.id, Assignment.activity_id == activity_id)
    if item_id is None:
        items = items.where(
            StudyItemRevision.status == "published",
            StudyItemRevision.uuid.in_({uuid for uuid, _ in approved}),
        ).order_by(StudyItemRevision.revision.desc(), StudyItemRevision.uuid)
    else:
        items = items.where(StudyItemRevision.id == item_id)
    for item in (await db.execute(items)).scalars().unique():
        evidence = approved.get((item.uuid, item.content_digest))
        if not isinstance(evidence, str) or not evidence.strip():
            continue
        try:
            command = CurriculumPublish(
                expected_version=item.version,
                expected_digest=item.content_digest,
                review_evidence=evidence,
            )
            _require_item_and_curriculum_approval(policies, item, curriculum, command)
            if release_source is None:
                if await _require_current_native_source(db, org, item) != activity_uuid:
                    continue
            else:
                retained = retained_native_item_source(
                    release_source.authorization.release.snapshot, org.id,
                    curriculum.owner_course_uuid, item.assignment_uuid,
                    item.source_task_uuid, item.source_question_uuid,
                )
                if retained is None or (
                    retained[0].source_digest != item.source_digest
                    or retained[1] != item.private_answer
                ):
                    continue
        except ValidationError:
            continue
        except HTTPException as error:
            if item_id is not None or error.status_code != 409:
                raise
            continue
        return PracticeAccess(
            curriculum,
            item,
            rules,
            policy,
            [int(p.id) for p in policies],
            activity_uuid,
            release_source,
        )
    if item_id is not None:
        raise studytomy_http_error(
            409, STALE_VERSION, "The reviewed practice item is unavailable."
        )
    raise studytomy_http_error(
        409,
        POLICY_NOT_APPROVED,
        "No reviewed practice item is available for this lesson.",
    )
