"""Approve and remove an expired private assessment archive."""

from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.assessment_archive import StudyAssessmentResponseArchive
from src.db.studytomy.governance import (
    StudyBusinessEvent,
    StudyPolicyBody,
    StudyPolicyKey,
)
from src.db.users import PublicUser, User
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_studytomy_staff_action,
)
from src.services.studytomy.assessment_integrity import round_has_open_integrity_concern
from src.services.studytomy.assessment_reviews import _appeal_receipt
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import lock_organization

APPROVED = "studytomy.assessment.archive_removal_approved"
REMOVED = "studytomy.assessment.archive_removed"


class ArchiveRemovalReceipt(BaseModel):
    reference: str
    round_uuid: str


async def remove_assessment_archive(
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    round_uuid: str,
    approval_uuid: str | None = None,
) -> ArchiveRemovalReceipt:
    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    await authorize_studytomy_staff_action(
        db,
        actor,
        org,
        StudytomyCapabilityName.PRIVACY_RETENTION_REVIEW,
        target_scope_kind="case",
        target_resource_uuid=round_uuid,
        hide_existence_on_denial=True,
        now=now,
    )
    (policy,) = await _current_policies(db, org, (StudyPolicyKey.D_13,), now)
    rules = StudyPolicyBody.model_validate(policy.body).specified_value("removal")
    if (
        not isinstance(rules, dict)
        or rules.get("managed_assessment_archive_removal_enabled") is not True
    ):
        raise HTTPException(409, "Current policy does not approve archive removal.")

    approval = None
    if approval_uuid is not None:
        approval = await db.scalar(
            select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.action == APPROVED,
                StudyBusinessEvent.resource_uuid == round_uuid,
                StudyBusinessEvent.event_uuid == approval_uuid,
            )
        )
        if approval is None:
            raise HTTPException(404, "Archive removal approval not found.")
        if (
            approval.actor_user_id == actor.id
            or approval.subject_uuid == actor.user_uuid
        ):
            raise HTTPException(
                403, "A different assigned reviewer must execute removal."
            )
        replay = await db.scalar(
            select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.action == REMOVED,
                StudyBusinessEvent.dedupe_key
                == f"assessment_archive_removed:{approval_uuid}",
            )
        )
        if replay is not None:
            return ArchiveRemovalReceipt(
                reference=replay.event_uuid, round_uuid=round_uuid
            )

    archive = await db.get(
        StudyAssessmentResponseArchive, round_uuid, with_for_update=True
    )
    if archive is None or archive.org_id != org.id:
        raise HTTPException(404, "Assessment archive not found.")
    if archive.user_id == actor.id:
        raise HTTPException(404, "Assessment archive not found.")
    cutoff = archive.retain_until
    if (cutoff.replace(tzinfo=UTC) if cutoff.tzinfo is None else cutoff) > now:
        raise HTTPException(
            409, "The assessment archive is still within its retention period."
        )
    appeals = (
        await db.execute(
            select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.action == "studytomy.assessment.appeal_requested",
                StudyBusinessEvent.operation_uuid == round_uuid,
            )
        )
    ).scalars()
    for appeal in appeals:
        if (await _appeal_receipt(db, appeal)).status in ("received", "reopened"):
            raise HTTPException(409, "An open appeal prevents archive removal.")
    if await round_has_open_integrity_concern(db, org.id, round_uuid):
        raise HTTPException(409, "An integrity concern is awaiting review.")

    snapshot = {
        "source_snapshot_digest": archive.content_digest,
        "retention_cutoff": cutoff.isoformat(),
    }
    if approval is not None and (
        approval.after_state != snapshot or approval.policy_refs != [policy.id]
    ):
        raise HTTPException(409, "The removal approval is stale.")
    operation_key = (
        f"assessment_archive_removed:{approval_uuid}"
        if approval
        else f"assessment_archive_approval:{round_uuid}:{policy.id}:{actor.id}"
    )
    if approval is None:
        existing = await db.scalar(
            select(StudyBusinessEvent).where(
                StudyBusinessEvent.org_id == org.id,
                StudyBusinessEvent.dedupe_key == operation_key,
            )
        )
        if existing is not None:
            return ArchiveRemovalReceipt(
                reference=existing.event_uuid, round_uuid=round_uuid
            )
    subject = await db.get(User, archive.user_id)
    event = await append_business_event(
        db,
        org_id=org.id,
        actor_user_id=actor.id,
        actor_capacity="privacy_reviewer",
        action=REMOVED if approval else APPROVED,
        reason_code="EXPIRED_ARCHIVE_REMOVED"
        if approval
        else "EXPIRED_ARCHIVE_REMOVAL_APPROVED",
        subject_uuid=subject.user_uuid,
        resource_kind="assignment_round",
        resource_uuid=round_uuid,
        operation_uuid=approval_uuid,
        policy_refs=[policy.id],
        after_state=snapshot,
        dedupe_key=operation_key,
    )
    if approval is not None:
        await db.delete(archive)
    await db.commit()
    return ArchiveRemovalReceipt(reference=event.event_uuid, round_uuid=round_uuid)
