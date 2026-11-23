"""Private, learner-scoped evidence file operations (TASK-007 slice).

This first slice supports eligibility evidence for a learner profile. Case and
progress-report attachments remain unavailable until their owning records and
recipient workflows exist. Object bytes stay in the configured storage
adapter; only a safe sidecar projection is exposed by the API.
"""

import asyncio
import hashlib
import re
from contextlib import aclosing
from datetime import datetime, timezone
from typing import AsyncIterator
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.family import (
    StudyLearnerProfile,
    StudyLearnerProfileStatus,
    StudyRelationship,
)
from src.db.studytomy.governance import (
    StudyOwnerFunction,
    StudyPolicyKey,
    inspect_study_policy_body,
)
from src.db.studytomy.operations import StudyEvidenceFile, StudyEvidenceFileRead
from src.db.users import PublicUser, User
from src.security.file_validation import validate_upload_stream
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_studytomy_staff_action,
)
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.idempotency import claim_idempotency, complete_idempotency
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization, lock_users
from src.services.utils.upload_content import open_content_stream, upload_content

_MAX_EVIDENCE_FILE_BYTES = 500 * 1024 * 1024
_SUPPORTED_MEDIA = {
    "application/pdf": ("document", ".pdf"),
    "image/jpeg": ("image", ".jpg"),
    "image/png": ("image", ".png"),
    "image/gif": ("image", ".gif"),
    "image/webp": ("image", ".webp"),
}
_RELEASE_ONLY_MEDIA = {"video/mp4", "video/webm", "text/vtt"}
_SAFE_RETENTION_CATEGORY = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def _policy_area_values(policy, area: str) -> dict:
    body, blockers = inspect_study_policy_body(policy.policy_key, policy.body)
    if body is None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POLICY_BODY_INVALID",
                "message": f"The approved {policy.policy_key} policy body is invalid.",
            },
        )
    if any(blockers.values()):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "POLICY_BODY_INCOMPLETE",
                "message": f"The approved {policy.policy_key} policy body is incomplete.",
            },
        )
    value = body.specified_value(area)
    return value if isinstance(value, dict) else {}


def _approved_file_rules(
    policies: list, *, retention_field: str = "learner_evidence_retention_category",
) -> tuple[int, set[str], str]:
    by_key = {policy.policy_key: policy for policy in policies}
    d13 = by_key.get(StudyPolicyKey.D_13.value)
    d15 = by_key.get(StudyPolicyKey.D_15.value)
    if d13 is None or d15 is None:
        raise HTTPException(status_code=409, detail="Current D-13 and D-15 policies are required")

    retention_category = _policy_area_values(d13, "retention").get(retention_field)
    if (
        not isinstance(retention_category, str)
        or not _SAFE_RETENTION_CATEGORY.fullmatch(retention_category)
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "RETENTION_CATEGORY_UNCONFIGURED",
                "message": "The approved D-13 policy must define a retention category for this evidence purpose.",
            },
        )

    d15_values = _policy_area_values(d15, "supported_formats")
    max_bytes = d15_values.get("max_bytes")
    media_types = d15_values.get("media_types")
    if (
        type(max_bytes) is not int
        or not 1 <= max_bytes <= _MAX_EVIDENCE_FILE_BYTES
        or not isinstance(media_types, list)
        or not media_types
        or any(
            not isinstance(value, str)
            or (value not in _SUPPORTED_MEDIA and value not in _RELEASE_ONLY_MEDIA)
            for value in media_types
        )
        or not any(value in _SUPPORTED_MEDIA for value in media_types)
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "EVIDENCE_FILE_POLICY_UNCONFIGURED",
                "message": "The approved D-15 policy must define a supported format list and bounded file size.",
            },
        )
    return max_bytes, set(media_types).intersection(_SUPPORTED_MEDIA), retention_category


def _stream_digest(file_binary) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    file_binary.seek(0)
    while True:
        chunk = file_binary.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
    file_binary.seek(0)
    return digest.hexdigest(), size


async def _validated_evidence_file(
    file: UploadFile, max_bytes: int, allowed_media_types: set[str],
) -> tuple[str, int, str]:
    extension = "." + (file.filename or "").rsplit(".", 1)[-1].lower()
    formats = {suffix: kind for kind, suffix in _SUPPORTED_MEDIA.values()}
    if extension == ".jpeg":
        extension = ".jpg"
    category = formats.get(extension)
    if category is None:
        raise HTTPException(status_code=415, detail="This evidence file format is not supported")
    category_limit = _MAX_EVIDENCE_FILE_BYTES if category == "document" else 15 * 1024 * 1024
    effective_max_bytes = min(max_bytes, category_limit)
    media_type = validate_upload_stream(file, [category], effective_max_bytes)
    if media_type not in allowed_media_types:
        raise HTTPException(status_code=415, detail="This evidence file format is not approved")
    content_sha256, size_bytes = await asyncio.to_thread(_stream_digest, file.file)
    if size_bytes <= 0 or size_bytes > effective_max_bytes:
        raise HTTPException(status_code=413, detail="Evidence file size is outside the approved limit")
    return content_sha256, size_bytes, media_type


async def _get_profile(
    db_session: AsyncSession,
    organization: Organization,
    learner_uuid: str,
    *,
    for_update: bool = False,
) -> StudyLearnerProfile:
    statement = select(StudyLearnerProfile).where(
        StudyLearnerProfile.org_id == organization.id,
        StudyLearnerProfile.learner_uuid == learner_uuid,
    )
    if for_update:
        statement = statement.with_for_update()
    profile = (await db_session.execute(statement)).scalars().first()
    if profile is None:
        raise HTTPException(status_code=404, detail="Learner profile not found")
    return profile


async def _authorize_evidence_action(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    profile: StudyLearnerProfile,
    now: datetime,
    *,
    require_pending: bool = False,
) -> list:
    if profile.status == StudyLearnerProfileStatus.CLOSED.value:
        raise HTTPException(status_code=404, detail="Learner profile not found")
    if require_pending and profile.status not in (
        StudyLearnerProfileStatus.PENDING.value,
        StudyLearnerProfileStatus.ACTIVE.value,
    ):
        raise HTTPException(
            status_code=409,
            detail="Evidence attachments are only enabled for pending or active learner eligibility reviews.",
        )
    await authorize_studytomy_staff_action(
        db_session,
        actor,
        organization,
        StudytomyCapabilityName.LEARNER_ELIGIBILITY_REVIEW,
        target_resource_uuid=profile.learner_uuid,
        target_scope_kind="learner",
        subject_user_id=profile.user_id,
        access_version=profile.access_version,
        hide_existence_on_denial=True,
        now=now,
    )
    await authorize_studytomy_staff_action(
        db_session,
        actor,
        organization,
        StudytomyCapabilityName.PRIVACY_RETENTION_REVIEW,
        target_resource_uuid=profile.learner_uuid,
        target_scope_kind="learner",
        subject_user_id=profile.user_id,
        access_version=profile.access_version,
        hide_existence_on_denial=True,
        now=now,
    )
    return await _current_policies(
        db_session,
        organization,
        (
            StudyPolicyKey.D_01,
            StudyPolicyKey.D_03,
            StudyPolicyKey.D_12,
            StudyPolicyKey.D_13,
            StudyPolicyKey.D_15,
        ),
        now,
    )


async def _file_read(
    db_session: AsyncSession,
    profile: StudyLearnerProfile,
    record: StudyEvidenceFile,
) -> StudyEvidenceFileRead:
    learner = await db_session.get(User, profile.user_id)
    if learner is None:
        raise HTTPException(status_code=409, detail="Learner identity is unavailable")
    return StudyEvidenceFileRead(
        evidence_uuid=record.evidence_uuid,
        learner_uuid=profile.learner_uuid,
        purpose=record.purpose,
        content_sha256=record.content_sha256,
        policy_version_ids=record.policy_version_ids,
        media_type=record.media_type,
        size_bytes=record.size_bytes,
        retention_category=record.retention_category,
        upload_status=record.upload_status,
        created_at=record.created_at,
        updated_at=record.updated_at,
        verified_at=record.verified_at,
    )


async def upload_eligibility_evidence(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    learner_uuid: str,
    file: UploadFile,
    idempotency_key: str,
) -> StudyEvidenceFileRead:
    """Store a private learner evidence attachment under D-12/D-13/D-15."""
    await lock_organization(db_session, organization.id)
    profile = await _get_profile(db_session, organization, learner_uuid, for_update=True)
    now = datetime.now(timezone.utc)
    policies = await _authorize_evidence_action(
        db_session, organization, actor, profile, now, require_pending=True
    )
    max_bytes, allowed_media_types, retention_category = _approved_file_rules(policies)
    record = await store_private_learner_evidence(
        db_session, organization, actor, profile, file, idempotency_key,
        policies=policies, file_rules=(max_bytes, allowed_media_types, retention_category), now=now,
    )
    return await _file_read(db_session, profile, record)


async def store_private_learner_evidence(
    db_session: AsyncSession, organization: Organization, actor: PublicUser, profile: StudyLearnerProfile,
    file: UploadFile, idempotency_key: str, *, policies: list, file_rules: tuple[int, set[str], str],
    now: datetime, record_request_event_uuid: str | None = None,
) -> StudyEvidenceFile:
    """Persist an authorized learner attachment with verified private bytes."""
    await lock_users(db_session, [actor.id, profile.user_id])
    return await _store_private_evidence(
        db_session, organization, actor, file, idempotency_key,
        purpose="record_request_evidence" if record_request_event_uuid else "eligibility_evidence",
        policy_ids=[int(policy.id) for policy in policies], file_rules=file_rules, now=now,
        profile=profile, record_request_event_uuid=record_request_event_uuid,
    )


async def _store_private_evidence(
    db_session: AsyncSession, organization: Organization, actor: PublicUser, file: UploadFile,
    idempotency_key: str, *, purpose: str, policy_ids: list[int],
    file_rules: tuple[int, set[str], str], now: datetime,
    profile: StudyLearnerProfile | None = None, record_request_event_uuid: str | None = None,
    safeguarding_case_uuid: str | None = None,
    relationship_uuid: str | None = None,
) -> StudyEvidenceFile:
    max_bytes, allowed_media_types, retention_category = file_rules
    profile_id = profile.id if profile is not None else None
    content_sha256, size_bytes, media_type = await _validated_evidence_file(file, max_bytes, allowed_media_types)
    canonical_extension = _SUPPORTED_MEDIA[media_type][1]
    request_payload = {
        "learner_uuid": profile.learner_uuid if profile is not None else None,
        "purpose": purpose,
        "content_sha256": content_sha256,
        "size_bytes": size_bytes,
        "media_type": media_type,
        "retention_category": retention_category,
        "policy_version_ids": policy_ids,
    }
    if relationship_uuid is not None:
        request_payload["relationship_uuid"] = relationship_uuid
    if record_request_event_uuid:
        request_payload["record_request_event_uuid"] = record_request_event_uuid
    if safeguarding_case_uuid is not None:
        request_payload["safeguarding_case_uuid"] = safeguarding_case_uuid
    replay = await claim_idempotency(
        db_session,
        org_id=organization.id,
        actor_user_id=actor.id,
        operation="studytomy.evidence.upload",
        idempotency_key=idempotency_key,
        request_payload=request_payload,
    )
    if replay is not None:
        existing_result = await db_session.execute(
            select(StudyEvidenceFile).where(
                StudyEvidenceFile.org_id == organization.id,
                StudyEvidenceFile.evidence_uuid == replay.resource_uuid,
                StudyEvidenceFile.learner_profile_id == profile_id,
                StudyEvidenceFile.relationship_uuid == relationship_uuid,
                StudyEvidenceFile.safeguarding_case_uuid == safeguarding_case_uuid,
                StudyEvidenceFile.purpose == purpose,
                StudyEvidenceFile.record_request_event_uuid == record_request_event_uuid,
                StudyEvidenceFile.upload_status == "ready",
            )
        )
        existing = existing_result.scalars().first()
        if existing is None:
            raise HTTPException(status_code=409, detail="The idempotent evidence upload is unavailable")
        return existing

    evidence_uuid = f"ev_{uuid4()}"
    filename = f"{evidence_uuid}{canonical_extension}"
    storage_reference = (
        f"content/orgs/{organization.org_uuid}/studytomy/evidence/{filename}"
    )
    record = StudyEvidenceFile(
        evidence_uuid=evidence_uuid,
        org_id=organization.id,
        learner_profile_id=profile_id,
        relationship_uuid=relationship_uuid,
        safeguarding_case_uuid=safeguarding_case_uuid,
        purpose=purpose,
        record_request_event_uuid=record_request_event_uuid,
        storage_reference=storage_reference,
        content_sha256=content_sha256,
        policy_version_ids=policy_ids,
        media_type=media_type,
        size_bytes=size_bytes,
        retention_category=retention_category,
        upload_status="uploading",
        uploaded_by_user_id=actor.id,
        created_at=now,
        updated_at=now,
    )
    try:
        await upload_content(
            directory="studytomy/evidence",
            type_of_dir="orgs",
            uuid=organization.org_uuid,
            file_binary=file.file,
            file_and_format=filename,
            allowed_formats=None,
        )
        await _verify_private_evidence(organization, record)
        record.upload_status = "ready"
        record.verified_at = datetime.now(timezone.utc)
        record.updated_at = record.verified_at
        db_session.add(record)
        await db_session.flush()
        await append_business_event(
            db_session,
            org_id=organization.id,
            actor_user_id=actor.id,
            actor_capacity=(
                StudyOwnerFunction.SAFEGUARDING.value if safeguarding_case_uuid is not None
                else StudyOwnerFunction.PRIVACY.value
            ),
            subject_uuid=profile.learner_uuid if profile is not None else safeguarding_case_uuid,
            resource_kind="evidence_file",
            resource_uuid=record.evidence_uuid,
            action="studytomy.evidence.uploaded",
            reason_code="EVIDENCE_FILE_UPLOADED",
            after_state={"status": record.upload_status, "resource_kind": record.purpose},
            policy_refs=policy_ids,
            dedupe_key=f"studytomy:evidence:{record.evidence_uuid}:uploaded",
        )
        await complete_idempotency(
            db_session,
            org_id=organization.id,
            actor_user_id=actor.id,
            operation="studytomy.evidence.upload",
            idempotency_key=idempotency_key,
            resource_kind="evidence_file",
            resource_uuid=record.evidence_uuid,
            response_status=201,
            response_projection={"evidence_uuid": record.evidence_uuid},
        )
    except Exception:
        await db_session.rollback()
        # Compensate for an object written before the database transaction
        # failed. The private storage key is never included in logs or errors.
        from src.services.courses.transfer.storage_utils import delete_storage_file

        try:
            await asyncio.to_thread(delete_storage_file, storage_reference)
        except Exception:
            pass
        raise
    # A failed commit acknowledgment does not prove that the transaction
    # rolled back. Preserve the object so a committed record can still replay.
    await db_session.commit()
    await db_session.refresh(record)
    return record


async def list_eligibility_evidence(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    learner_uuid: str,
) -> list[StudyEvidenceFileRead]:
    profile = await _get_profile(db_session, organization, learner_uuid)
    now = datetime.now(timezone.utc)
    policies = await _authorize_evidence_action(db_session, organization, actor, profile, now)
    current_policy_ids = [int(policy.id) for policy in policies]
    result = await db_session.execute(
        select(StudyEvidenceFile)
        .where(
            StudyEvidenceFile.org_id == organization.id,
            StudyEvidenceFile.learner_profile_id == profile.id,
            StudyEvidenceFile.purpose == "eligibility_evidence",
            StudyEvidenceFile.upload_status == "ready",
        )
        .order_by(StudyEvidenceFile.created_at.desc(), StudyEvidenceFile.id.desc())
        .limit(100)
    )
    return [
        await _file_read(db_session, profile, record)
        for record in result.scalars().all()
        if record.policy_version_ids == current_policy_ids
    ]


async def open_eligibility_evidence(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    learner_uuid: str,
    evidence_uuid: str,
) -> tuple[StudyEvidenceFileRead, AsyncIterator[bytes]]:
    profile = await _get_profile(db_session, organization, learner_uuid)
    policies = await _authorize_evidence_action(
        db_session, organization, actor, profile, datetime.now(timezone.utc)
    )
    result = await db_session.execute(
        select(StudyEvidenceFile).where(
            StudyEvidenceFile.org_id == organization.id,
            StudyEvidenceFile.learner_profile_id == profile.id,
            StudyEvidenceFile.evidence_uuid == evidence_uuid,
            StudyEvidenceFile.purpose == "eligibility_evidence",
            StudyEvidenceFile.upload_status == "ready",
        )
    )
    record = result.scalars().first()
    if record is None:
        raise HTTPException(status_code=404, detail="Evidence file not found")
    current_policy_ids = [int(policy.id) for policy in policies]
    if record.policy_version_ids != current_policy_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "EVIDENCE_POLICY_STALE",
                "message": "The evidence is unavailable until the current privacy, retention and accessibility policy is reviewed.",
            },
        )

    return await _file_read(db_session, profile, record), await _open_private_evidence(organization, record)


async def _get_relationship_and_profile(
    db_session: AsyncSession,
    organization: Organization,
    relationship_uuid: str,
    *,
    for_update: bool = False,
) -> tuple[StudyRelationship, StudyLearnerProfile]:
    rel_q = select(StudyRelationship).where(
        StudyRelationship.org_id == organization.id,
        StudyRelationship.relationship_uuid == relationship_uuid,
    )
    if for_update:
        rel_q = rel_q.with_for_update()
    relationship = (await db_session.execute(rel_q)).scalars().first()
    if relationship is None:
        raise HTTPException(status_code=404, detail="Relationship request not found")
    prof_q = select(StudyLearnerProfile).where(
        StudyLearnerProfile.org_id == organization.id,
        StudyLearnerProfile.id == relationship.learner_profile_id,
    )
    if for_update:
        prof_q = prof_q.with_for_update()
    profile = (await db_session.execute(prof_q)).scalars().first()
    if profile is None or profile.status == StudyLearnerProfileStatus.CLOSED.value:
        raise HTTPException(status_code=404, detail="Relationship request not found")
    return relationship, profile


async def _authorize_relationship_evidence_action(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    relationship: StudyRelationship,
    profile: StudyLearnerProfile,
    now: datetime,
) -> list:
    if int(actor.id) in {int(profile.user_id), int(relationship.adult_user_id)}:
        raise HTTPException(status_code=403, detail="A reviewer cannot access evidence for their own relationship.")
    await authorize_studytomy_staff_action(
        db_session,
        actor,
        organization,
        StudytomyCapabilityName.FAMILY_AUTHORITY_REVIEW,
        target_resource_uuid=profile.learner_uuid,
        target_scope_kind="learner",
        subject_user_id=profile.user_id,
        access_version=profile.access_version,
        hide_existence_on_denial=True,
        now=now,
    )
    await authorize_studytomy_staff_action(
        db_session,
        actor,
        organization,
        StudytomyCapabilityName.PRIVACY_RETENTION_REVIEW,
        target_resource_uuid=profile.learner_uuid,
        target_scope_kind="learner",
        subject_user_id=profile.user_id,
        access_version=profile.access_version,
        hide_existence_on_denial=True,
        now=now,
    )
    return await _current_policies(
        db_session,
        organization,
        (
            StudyPolicyKey.D_01,
            StudyPolicyKey.D_03,
            StudyPolicyKey.D_12,
            StudyPolicyKey.D_13,
            StudyPolicyKey.D_15,
        ),
        now,
    )


async def upload_relationship_evidence(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    relationship_uuid: str,
    file: UploadFile,
    idempotency_key: str,
) -> StudyEvidenceFileRead:
    """Store a private guardian-authority evidence attachment bound to one relationship."""
    await lock_organization(db_session, organization.id)
    relationship, profile = await _get_relationship_and_profile(
        db_session, organization, relationship_uuid, for_update=True
    )
    now = datetime.now(timezone.utc)
    policies = await _authorize_relationship_evidence_action(
        db_session, organization, actor, relationship, profile, now
    )
    max_bytes, allowed_media_types, retention_category = _approved_file_rules(policies)
    await lock_users(db_session, [actor.id, profile.user_id, relationship.adult_user_id])
    record = await _store_private_evidence(
        db_session,
        organization,
        actor,
        file,
        idempotency_key,
        purpose="guardian_authority_evidence",
        policy_ids=[int(policy.id) for policy in policies],
        file_rules=(max_bytes, allowed_media_types, retention_category),
        now=now,
        profile=profile,
        relationship_uuid=relationship.relationship_uuid,
    )
    return await _file_read(db_session, profile, record)


async def list_relationship_evidence(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    relationship_uuid: str,
) -> list[StudyEvidenceFileRead]:
    relationship, profile = await _get_relationship_and_profile(
        db_session, organization, relationship_uuid
    )
    policies = await _authorize_relationship_evidence_action(
        db_session, organization, actor, relationship, profile, datetime.now(timezone.utc)
    )
    current_policy_ids = [int(policy.id) for policy in policies]
    result = await db_session.execute(
        select(StudyEvidenceFile)
        .where(
            StudyEvidenceFile.org_id == organization.id,
            StudyEvidenceFile.learner_profile_id == profile.id,
            StudyEvidenceFile.relationship_uuid == relationship.relationship_uuid,
            StudyEvidenceFile.purpose == "guardian_authority_evidence",
            StudyEvidenceFile.upload_status == "ready",
        )
        .order_by(StudyEvidenceFile.created_at.desc(), StudyEvidenceFile.id.desc())
    )
    return [
        await _file_read(db_session, profile, record)
        for record in result.scalars().all()
        if record.policy_version_ids == current_policy_ids
    ]


async def open_relationship_evidence(
    db_session: AsyncSession,
    organization: Organization,
    actor: PublicUser,
    relationship_uuid: str,
    evidence_uuid: str,
) -> tuple[StudyEvidenceFileRead, AsyncIterator[bytes]]:
    relationship, profile = await _get_relationship_and_profile(
        db_session, organization, relationship_uuid
    )
    policies = await _authorize_relationship_evidence_action(
        db_session, organization, actor, relationship, profile, datetime.now(timezone.utc)
    )
    result = await db_session.execute(
        select(StudyEvidenceFile).where(
            StudyEvidenceFile.org_id == organization.id,
            StudyEvidenceFile.learner_profile_id == profile.id,
            StudyEvidenceFile.relationship_uuid == relationship.relationship_uuid,
            StudyEvidenceFile.evidence_uuid == evidence_uuid,
            StudyEvidenceFile.purpose == "guardian_authority_evidence",
            StudyEvidenceFile.upload_status == "ready",
        )
    )
    record = result.scalars().first()
    if record is None:
        raise HTTPException(status_code=404, detail="Evidence file not found")
    current_policy_ids = [int(policy.id) for policy in policies]
    if record.policy_version_ids != current_policy_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "EVIDENCE_POLICY_STALE",
                "message": "The evidence is unavailable until the current privacy, retention and accessibility policy is reviewed.",
            },
        )
    return await _file_read(db_session, profile, record), await _open_private_evidence(organization, record)


async def _verify_private_evidence(
    organization: Organization, record: StudyEvidenceFile,
) -> None:
    digest = hashlib.sha256()
    size = 0
    async with aclosing(await _open_private_evidence(organization, record)) as chunks:
        async for chunk in chunks:
            size += len(chunk)
            if size > record.size_bytes:
                break
            digest.update(chunk)
    if size != record.size_bytes or digest.hexdigest() != record.content_sha256:
        raise HTTPException(status_code=503, detail="Private evidence storage verification failed")


async def _open_private_evidence(
    organization: Organization, record: StudyEvidenceFile,
) -> AsyncIterator[bytes]:
    expected_prefix = f"content/orgs/{organization.org_uuid}/studytomy/evidence/"
    if not record.storage_reference.startswith(expected_prefix):
        raise HTTPException(status_code=409, detail="Evidence storage reference is invalid")
    filename = record.storage_reference[len(expected_prefix) :]
    expected_extension = _SUPPORTED_MEDIA.get(record.media_type)
    if (
        expected_extension is None
        or filename != f"{record.evidence_uuid}{expected_extension[1]}"
    ):
        raise HTTPException(status_code=409, detail="Evidence storage reference is invalid")
    return await open_content_stream(
        directory="studytomy/evidence",
        type_of_dir="orgs",
        uuid=organization.org_uuid,
        file_and_format=filename,
    )
