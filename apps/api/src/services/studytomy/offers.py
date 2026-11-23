"""Private offer draft authoring and immutable proposed-term versions (TASK-017)."""

import hashlib
import json
from datetime import UTC, datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.studytomy.commerce import (
    StudyOffer,
    StudyOfferDraftCreate,
    StudyOfferDraftRead,
    StudyOfferDraftRevision,
    StudyOfferResourceKind,
    StudyOfferTerms,
    StudyOfferVersion,
)
from src.db.studytomy.education import StudyCourseRelease, StudyCourseReview
from src.db.studytomy.governance import StudyPolicyKey, inspect_study_policy_body
from src.db.users import PublicUser
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_studytomy_staff_action,
)
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.capacity import resolve_finite_capacity_terms
from src.services.studytomy.idempotency import (
    canonical_request_hash,
    claim_idempotency,
    complete_idempotency,
)
from src.services.studytomy.learners import _active_policy
from src.services.studytomy.policies import lock_organization
from src.services.studytomy.prerequisites import ungated_by_assessed_pass
from src.services.studytomy.releases import load_release_policy_binding


def _serialize_terms(terms: StudyOfferTerms) -> dict:
    data = terms.model_dump(mode="json", exclude_none=False)
    references = list(data["resources"] or [])
    references.extend(p["resource"] for p in data["prerequisites"] or [] if p.get("resource"))
    for reference in references:
        for field in ("release_uuid", "release_sha256"):
            if reference.get(field) is None:
                reference.pop(field, None)
    return data


def _terms_snapshot(
    terms: StudyOfferTerms,
    offer_kind: str,
) -> tuple[dict, str]:
    data = _serialize_terms(terms)
    encoded = json.dumps(
        {"offer_kind": offer_kind, "terms": data},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return data, hashlib.sha256(encoded).hexdigest()


async def _authorize_draft_scope(
    db_session: AsyncSession,
    current_user: PublicUser,
    organization: Organization,
    offer_uuid: str,
) -> None:
    await authorize_studytomy_staff_action(
        db_session,
        current_user,
        organization,
        StudytomyCapabilityName.OFFER_PUBLICATION,
        target_resource_uuid=offer_uuid,
        target_scope_kind="offer",
        hide_existence_on_denial=True,
    )


async def _snapshot_resource_titles(
    db_session: AsyncSession,
    organization: Organization,
    terms: StudyOfferTerms,
    *,
    require_current_release_policies_at: datetime | None = None,
) -> StudyOfferTerms:
    references = list(terms.resources or [])
    references.extend(
        prerequisite.resource
        for prerequisite in terms.prerequisites or []
        if prerequisite.resource is not None
    )
    for reference in references:
        if reference.kind == StudyOfferResourceKind.PROGRAMME_CONTENT:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "PROGRAMME_CONTENT_UNAVAILABLE",
                    "message": "Programme-content records are not available in this implementation.",
                },
            )
    course_uuids = {
        reference.resource_uuid
        for reference in references
        if reference.kind == StudyOfferResourceKind.COURSE
    }
    title_by_uuid: dict[str, str] = {}
    managed_courses: set[str] = set()
    if course_uuids:
        result = await db_session.execute(
            select(Course.course_uuid, Course.name, Course.studytomy_managed).where(
                Course.org_id == organization.id,
                Course.course_uuid.in_(course_uuids),
            )
        )
        courses = result.all()
        title_by_uuid = {
            course_uuid: title.strip()
            for course_uuid, title, _ in courses
            if isinstance(title, str) and title.strip()
        }
        if course_uuids != set(title_by_uuid):
            raise HTTPException(status_code=404, detail="Referenced course not found")
        managed_courses = {course_uuid for course_uuid, _, managed in courses if managed}

    data = terms.model_dump(mode="json", exclude_none=False)
    projected = list(data["resources"] or [])
    projected.extend(p["resource"] for p in data["prerequisites"] or [] if p.get("resource"))
    release_uuids = {r["release_uuid"] for r in projected if r["release_uuid"]}
    releases = {}
    if release_uuids:
        rows = (await db_session.execute(
            select(StudyCourseRelease)
            .join(StudyCourseReview, StudyCourseReview.id == StudyCourseRelease.review_id)
            .where(
                StudyCourseRelease.org_id == organization.id,
                StudyCourseRelease.uuid.in_(release_uuids),
                StudyCourseRelease.policy_binding.is_not(None),
                StudyCourseReview.org_id == organization.id,
                StudyCourseReview.course_uuid == StudyCourseRelease.course_uuid,
                StudyCourseReview.revision_sha256 == StudyCourseRelease.snapshot_sha256,
                StudyCourseReview.decision == "approved",
            )
        )).scalars().all()
        releases = {(row.course_uuid, row.uuid): row for row in rows}
    current_binding = None
    progression_body = None
    for reference in projected:
        course_uuid = reference["resource_uuid"]
        reference["title_snapshot"] = title_by_uuid[course_uuid]
        reference["release_sha256"] = None
        if reference["release_uuid"] is None:
            continue
        release = releases.get((course_uuid, reference["release_uuid"]))
        if release is None or course_uuid not in managed_courses:
            raise HTTPException(status_code=404, detail="Referenced course release not found")
        if require_current_release_policies_at is not None:
            if current_binding is None:
                binding = await load_release_policy_binding(
                    db_session, organization, require_current_release_policies_at,
                )
                current_binding = binding.model_dump(mode="json")
                progression = await _active_policy(
                    db_session, organization, StudyPolicyKey.D_09, require_current_release_policies_at,
                )
                version = binding.versions[StudyPolicyKey.D_09]
                if progression.id == version.policy_id and canonical_request_hash(progression.body) == version.body_sha256:
                    progression_body, _ = inspect_study_policy_body(StudyPolicyKey.D_09, progression.body)
            if release.policy_binding != current_binding:
                raise HTTPException(status_code=409, detail={
                    "code": "OFFER_COURSE_RELEASE_POLICY_CHANGED",
                    "message": "The reviewed release does not bind the current policy decisions.",
                })
            if progression_body is None or not ungated_by_assessed_pass(progression_body, course_uuid):
                raise HTTPException(status_code=409, detail={
                    "code": "OFFER_ASSESSED_PASS_UNAVAILABLE",
                    "message": "The offer requires assessed-pass progression that is unavailable.",
                })
        course_snapshot = release.snapshot.get("course")
        title = course_snapshot.get("name") if isinstance(course_snapshot, dict) else None
        if canonical_request_hash(release.snapshot) != release.snapshot_sha256:
            raise HTTPException(status_code=409, detail="Reviewed course release failed integrity verification")
        if not isinstance(title, str) or not title.strip():
            raise HTTPException(status_code=409, detail="Reviewed course release has no usable title")
        reference["title_snapshot"] = title.strip()
        reference["release_sha256"] = release.snapshot_sha256
    return StudyOfferTerms.model_validate(data)


async def _load_version(
    db_session: AsyncSession,
    offer: StudyOffer,
    version_no: int,
) -> StudyOfferVersion:
    result = await db_session.execute(
        select(StudyOfferVersion).where(
            StudyOfferVersion.offer_id == offer.id,
            StudyOfferVersion.version_no == version_no,
        )
    )
    version = result.scalars().first()
    if version is None:
        raise HTTPException(status_code=404, detail="Offer draft version not found")
    return version


def _draft_read(
    offer: StudyOffer,
    version: StudyOfferVersion,
) -> StudyOfferDraftRead:
    terms = StudyOfferTerms.model_validate(version.terms)
    return StudyOfferDraftRead(
        offer_uuid=offer.offer_uuid,
        offer_kind=offer.offer_kind,
        status=offer.status,
        version_no=version.version_no,
        current_version=offer.current_version_no,
        current_version_uuid=version.version_uuid,
        content_sha256=version.content_sha256,
        terms=terms,
        validation_blockers=terms.completion_blockers(),
        created_at=offer.created_at,
    )


async def create_offer_draft(
    db_session: AsyncSession,
    organization: Organization,
    current_user: PublicUser,
    request: StudyOfferDraftCreate,
    idempotency_key: str,
) -> StudyOfferDraftRead:
    locked_organization = await lock_organization(db_session, int(organization.id))
    await _authorize_draft_scope(
        db_session, current_user, locked_organization, request.offer_uuid
    )
    replay = await claim_idempotency(
        db_session,
        org_id=int(locked_organization.id),
        actor_user_id=int(current_user.id),
        operation="studytomy.offer.draft.create",
        idempotency_key=idempotency_key,
        request_payload={
            **request.model_dump(mode="json", exclude={"terms"}),
            "terms": _serialize_terms(request.terms),
        },
    )
    if replay is not None:
        if replay.resource_uuid != request.offer_uuid:
            raise HTTPException(status_code=409, detail="Offer draft replay is unavailable")
        result = await db_session.execute(
            select(StudyOffer).where(
                StudyOffer.org_id == locked_organization.id,
                StudyOffer.offer_uuid == replay.resource_uuid,
            )
        )
        offer = result.scalars().first()
        if offer is None:
            raise HTTPException(status_code=409, detail="Offer draft replay is unavailable")
        return _draft_read(offer, await _load_version(db_session, offer, 1))

    existing = await db_session.execute(
        select(StudyOffer.id).where(StudyOffer.offer_uuid == request.offer_uuid)
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Offer UUID already exists in this organization")

    terms = await _snapshot_resource_titles(
        db_session, locked_organization, request.terms
    )
    snapshot, content_hash = _terms_snapshot(terms, request.offer_kind.value)
    offer = StudyOffer(
        offer_uuid=request.offer_uuid,
        org_id=int(locked_organization.id),
        offer_kind=request.offer_kind.value,
        status="draft",
        current_version_no=1,
        created_by_user_id=int(current_user.id),
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(offer)
    await db_session.flush()
    version = StudyOfferVersion(
        offer_id=int(offer.id),
        version_no=1,
        terms=snapshot,
        content_sha256=content_hash,
        created_by_user_id=int(current_user.id),
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(version)
    await db_session.flush()
    await append_business_event(
        db_session,
        org_id=int(locked_organization.id),
        actor_user_id=int(current_user.id),
        actor_capacity="business_owner",
        resource_kind="offer",
        resource_uuid=offer.offer_uuid,
        action="studytomy.offer.draft_created",
        reason_code="OFFER_DRAFT_CREATED",
        after_state={"status": "draft", "version": 1},
    )
    await complete_idempotency(
        db_session,
        org_id=int(locked_organization.id),
        actor_user_id=int(current_user.id),
        operation="studytomy.offer.draft.create",
        idempotency_key=idempotency_key,
        resource_kind="offer",
        resource_uuid=offer.offer_uuid,
        response_status=201,
        response_projection={"version": 1},
    )
    await db_session.commit()
    await db_session.refresh(offer)
    return _draft_read(offer, version)


async def revise_offer_draft(
    db_session: AsyncSession,
    organization: Organization,
    current_user: PublicUser,
    offer_uuid: str,
    request: StudyOfferDraftRevision,
    idempotency_key: str,
) -> StudyOfferDraftRead:
    locked_organization = await lock_organization(db_session, int(organization.id))
    await _authorize_draft_scope(
        db_session, current_user, locked_organization, offer_uuid
    )
    result = await db_session.execute(
        select(StudyOffer)
        .where(
            StudyOffer.org_id == locked_organization.id,
            StudyOffer.offer_uuid == offer_uuid,
        )
        .with_for_update()
    )
    offer = result.scalars().first()
    if offer is None:
        raise HTTPException(status_code=404, detail="Offer draft not found")

    replay = await claim_idempotency(
        db_session,
        org_id=int(locked_organization.id),
        actor_user_id=int(current_user.id),
        operation="studytomy.offer.draft.revise",
        idempotency_key=idempotency_key,
        request_payload={
            "offer_uuid": offer_uuid,
            "expected_version": request.expected_version,
            "terms": _serialize_terms(request.terms),
        },
    )
    if replay is not None:
        projection = replay.response_projection or {}
        replay_version = projection.get("version")
        if (
            replay.resource_uuid != offer_uuid
            or not isinstance(replay_version, int)
            or replay_version < 1
        ):
            raise HTTPException(status_code=409, detail="Offer revision replay is unavailable")
        return _draft_read(offer, await _load_version(db_session, offer, replay_version))

    if request.expected_version != offer.current_version_no:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STALE_OFFER_DRAFT_VERSION",
                "current_version": offer.current_version_no,
                "message": "Reload the latest draft before saving a new version.",
            },
        )
    terms = await _snapshot_resource_titles(
        db_session, locked_organization, request.terms
    )
    snapshot, content_hash = _terms_snapshot(terms, offer.offer_kind)
    prior = await _load_version(db_session, offer, offer.current_version_no)
    if prior.content_sha256 == content_hash:
        raise HTTPException(status_code=409, detail="The proposed terms are unchanged")

    next_version_no = offer.current_version_no + 1
    version = StudyOfferVersion(
        offer_id=int(offer.id),
        version_no=next_version_no,
        terms=snapshot,
        content_sha256=content_hash,
        created_by_user_id=int(current_user.id),
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(version)
    offer.current_version_no = next_version_no
    db_session.add(offer)
    await db_session.flush()
    await append_business_event(
        db_session,
        org_id=int(locked_organization.id),
        actor_user_id=int(current_user.id),
        actor_capacity="business_owner",
        resource_kind="offer",
        resource_uuid=offer.offer_uuid,
        action="studytomy.offer.draft_revised",
        reason_code="OFFER_DRAFT_REVISED",
        before_state={"status": "draft", "version": prior.version_no},
        after_state={"status": "draft", "version": next_version_no},
    )
    await complete_idempotency(
        db_session,
        org_id=int(locked_organization.id),
        actor_user_id=int(current_user.id),
        operation="studytomy.offer.draft.revise",
        idempotency_key=idempotency_key,
        resource_kind="offer",
        resource_uuid=offer.offer_uuid,
        response_status=200,
        response_projection={"version": next_version_no},
    )
    await db_session.commit()
    await db_session.refresh(offer)
    return _draft_read(offer, version)


async def get_offer_draft(
    db_session: AsyncSession,
    organization: Organization,
    current_user: PublicUser,
    offer_uuid: str,
    version_no: int | None = None,
) -> StudyOfferDraftRead:
    await _authorize_draft_scope(db_session, current_user, organization, offer_uuid)
    result = await db_session.execute(
        select(StudyOffer).where(
            StudyOffer.org_id == organization.id,
            StudyOffer.offer_uuid == offer_uuid,
        )
    )
    offer = result.scalars().first()
    if offer is None:
        raise HTTPException(status_code=404, detail="Offer draft not found")
    selected_version = version_no or offer.current_version_no
    if selected_version > offer.current_version_no:
        raise HTTPException(status_code=404, detail="Offer draft version not found")
    return _draft_read(offer, await _load_version(db_session, offer, selected_version))


class OfferPolicyBlocker(BaseModel):
    policy_key: StudyPolicyKey
    code: str


class OfferDraftReadiness(BaseModel):
    version_uuid: str
    validation_blockers: list[str]
    policy_blockers: list[OfferPolicyBlocker]


async def get_offer_draft_readiness(
    db: AsyncSession, org: Organization, actor: PublicUser, offer_uuid: str,
) -> OfferDraftReadiness:
    draft = await get_offer_draft(db, org, actor, offer_uuid)
    now = datetime.now(UTC)
    validation_blockers = list(draft.validation_blockers)
    if draft.terms.capacity_is_limited is True:
        try:
            await resolve_finite_capacity_terms(db, org, offer_uuid, draft.terms, now)
        except HTTPException as error:
            if error.status_code != 409:
                raise
            validation_blockers.append("OFFER_CAPACITY_POLICY_UNAVAILABLE")
    pinned = [
        resource for resource in draft.terms.resources or []
        if resource.release_uuid is not None
    ]
    if pinned:
        try:
            current = await _snapshot_resource_titles(
                db, org, StudyOfferTerms(resources=pinned),
                require_current_release_policies_at=now,
            )
        except HTTPException as error:
            if error.status_code not in (404, 409):
                raise
            code = error.detail.get("code") if isinstance(error.detail, dict) else None
            if code in {"OFFER_COURSE_RELEASE_POLICY_CHANGED", "OFFER_ASSESSED_PASS_UNAVAILABLE"}:
                validation_blockers.append(code)
            elif code and (code.startswith("POLICY_") or code == "RELEASE_RETENTION_UNCONFIGURED"):
                validation_blockers.append("OFFER_COURSE_RELEASE_POLICY_UNAVAILABLE")
            else:
                validation_blockers.append("OFFER_COURSE_RELEASE_UNAVAILABLE")
        else:
            if any(
                saved.release_sha256 != resolved.release_sha256
                for saved, resolved in zip(pinned, current.resources or [], strict=True)
            ):
                validation_blockers.append("OFFER_COURSE_RELEASE_CHANGED")
    blockers = []
    for key in (StudyPolicyKey.D_02, StudyPolicyKey.D_05, StudyPolicyKey.D_18, StudyPolicyKey.D_20):
        try:
            await _active_policy(db, org, key, now)
        except HTTPException as error:
            if error.status_code != 409 or not isinstance(error.detail, dict):
                raise
            blockers.append(OfferPolicyBlocker(policy_key=key, code=error.detail["code"]))
    return OfferDraftReadiness(
        version_uuid=draft.current_version_uuid,
        validation_blockers=validation_blockers,
        policy_blockers=blockers,
    )
