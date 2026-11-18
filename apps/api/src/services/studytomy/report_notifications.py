"""Optional progress-report delivery through the shared durable outbox."""

from datetime import UTC, datetime
from html import escape
from urllib.parse import urlencode, urlsplit

from fastapi import HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.concurrency import run_in_threadpool

from src.db.organizations import Organization
from src.db.studytomy.adaptive_reports import StudyProgressReport, StudyReportRecipient
from src.db.studytomy.family import StudyLearnerProfile, StudySharingGrant
from src.db.studytomy.governance import StudyPolicyKey
from src.db.studytomy.operations import StudyOutboxPurpose, StudyOutboxRecord
from src.db.users import PublicUser, User
from src.services.demo.flags import is_demo_email
from src.services.email.utils import get_org_signup_base_url, send_email
from src.services.studytomy.adaptive_diagnostics import _policy_body
from src.services.studytomy.adaptive_reports import (
    _latest_report,
    _published_report_context,
    _report_authority,
)
from src.services.studytomy.errors import POLICY_NOT_APPROVED, studytomy_http_error
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.outbox import enqueue_delivery
from src.services.studytomy.policies import lock_organization
from src.services.studytomy.sharing import current_independent_sharing_grant
from src.services.studytomy.worker import (
    DeliveryAuthorization,
    ProviderOutcomeUnknown,
    ProviderReceipt,
    run_delivery_batch,
)

_EMAIL_SCOPE = "adaptive_report_email"
_PURPOSE = StudyOutboxPurpose.PROGRESS_REPORT


class ReportDeliveryStatus(BaseModel):
    status: str
    recipient_count: int


class ReportNotificationRead(BaseModel):
    uuid: str
    version: int
    available_count: int
    deliveries: list[ReportDeliveryStatus]


def _delivery_status_for_report(status: str) -> str:
    """A provider acknowledgment is not proof the message was delivered."""
    return "provider_accepted" if status == "sent" else status


async def _approved_email(db: AsyncSession, org: Organization, now: datetime) -> bool:
    policies = await _current_policies(
        db, org, (StudyPolicyKey.D_12, StudyPolicyKey.D_13, StudyPolicyKey.D_18), now
    )
    uses = _policy_body(policies, StudyPolicyKey.D_12).specified_value(
        "information_uses"
    )
    records = _policy_body(policies, StudyPolicyKey.D_13).specified_value(
        "record_access"
    )
    terms = _policy_body(policies, StudyPolicyKey.D_18).specified_value(
        "customer_terms"
    )
    key = "progress_report_notifications"
    return (
        isinstance(uses, dict)
        and uses.get(key) == ["email"]
        and isinstance(records, dict)
        and records.get(key) == "authenticated_link_only"
        and isinstance(terms, dict)
        and terms.get(key) is True
    )


async def _eligible_recipient(
    db: AsyncSession,
    org: Organization,
    row: StudyProgressReport,
    recipient: StudyReportRecipient,
    revision_uuid: str,
    now: datetime,
) -> tuple[User, StudySharingGrant] | None:
    user = (
        await db.execute(
            select(User)
            .where(User.id == recipient.recipient_user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if (
        user is None
        or not user.email_verified
        or not user.email
        or is_demo_email(user.email)
    ):
        return None
    profile = await db.get(StudyLearnerProfile, row.learner_profile_id)
    if profile is None:
        return None
    grant = await current_independent_sharing_grant(
        db, org, profile, user.id, _EMAIL_SCOPE, now=now
    )
    if grant is None or (grant.id, grant.authority_version) != (
        recipient.sharing_grant_id,
        recipient.grant_version,
    ):
        return None
    actor = PublicUser.model_validate(user, from_attributes=True)
    context = await _published_report_context(db, org, actor, row, revision_uuid, now)
    return (user, grant) if context is not None else None


async def request_report_notification(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    report_uuid: str,
    expected_version: int | None = None,
) -> ReportNotificationRead:
    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    row, learner_uuid, revision_uuid = await _latest_report(db, org, report_uuid)
    _, _, _, policy_ids = await _report_authority(
        request, db, org, actor, learner_uuid, revision_uuid, now
    )
    if expected_version is not None and row.version != expected_version:
        raise studytomy_http_error(
            409, "STALE_VERSION", "Reload the current published report."
        )
    if (
        row.status != "published"
        or row.policy_version_ids != policy_ids
        or not await _approved_email(db, org, now)
    ):
        raise studytomy_http_error(
            409,
            POLICY_NOT_APPROVED,
            "Report email requires an approved electronic notification policy and a current published report.",
        )
    recipients = (
        (
            await db.execute(
                select(StudyReportRecipient)
                .where(
                    StudyReportRecipient.org_id == org.id,
                    StudyReportRecipient.report_id == row.id,
                    StudyReportRecipient.available_at <= now,
                )
                .order_by(StudyReportRecipient.recipient_user_id)
            )
        )
        .scalars()
        .all()
    )
    status_counts: dict[str, int] = {}
    for recipient in recipients:
        current = await _eligible_recipient(db, org, row, recipient, revision_uuid, now)
        if current is None:
            status = "suppressed"
        else:
            user, grant = current
            delivery, _ = await enqueue_delivery(
                db,
                org_id=org.id,
                recipient_user_id=user.id,
                purpose=_PURPOSE,
                resource_kind="progress_report",
                resource_uuid=row.uuid,
                resource_version=row.version,
                authority_reference_uuid=grant.grant_uuid,
                policy_refs=policy_ids,
                created_by_user_id=actor.id,
                dedupe_key=f"{row.uuid}:{row.version}:{recipient.id}:report-email",
            )
            status = _delivery_status_for_report(delivery.status)
        status_counts[status] = status_counts.get(status, 0) + 1
    receipt = ReportNotificationRead(
        uuid=row.uuid,
        version=row.version,
        available_count=len(recipients),
        deliveries=[
            ReportDeliveryStatus(status=status, recipient_count=count)
            for status, count in sorted(status_counts.items())
        ],
    )
    await db.commit()
    return receipt


async def _dispatch_context(db: AsyncSession, record: StudyOutboxRecord):
    await lock_organization(db, record.org_id)
    org = await db.get(Organization, record.org_id)
    if org is None or record.purpose != _PURPOSE.value:
        return None
    now = datetime.now(UTC)
    try:
        row, _, revision_uuid = await _latest_report(db, org, record.resource_uuid)
        approved = await _approved_email(db, org, now)
    except HTTPException as error:
        if error.status_code not in (403, 404, 409):
            raise
        return None
    if (
        not approved
        or row.status != "published"
        or row.version != record.resource_version
        or row.policy_version_ids != record.policy_refs
    ):
        return None
    recipient = (
        await db.execute(
            select(StudyReportRecipient).where(
                StudyReportRecipient.org_id == org.id,
                StudyReportRecipient.report_id == row.id,
                StudyReportRecipient.recipient_user_id == record.recipient_user_id,
                StudyReportRecipient.available_at <= now,
            )
        )
    ).scalar_one_or_none()
    if recipient is None:
        return None
    eligible = await _eligible_recipient(db, org, row, recipient, revision_uuid, now)
    if eligible is None or eligible[1].grant_uuid != record.authority_reference_uuid:
        return None
    return org, row, eligible[0]


async def revalidate_report_delivery(
    db: AsyncSession, record: StudyOutboxRecord
) -> DeliveryAuthorization:
    context = await _dispatch_context(db, record)
    allowed = context is not None
    return DeliveryAuthorization(
        purpose_allowed=allowed,
        consent_current=allowed,
        relationship_current=allowed,
        resource_complete=allowed,
        resource_version=record.resource_version if allowed else None,
        denial_code=None if allowed else "REPORT_RECIPIENT_NOT_AUTHORIZED",
    )


async def send_report_delivery(
    db: AsyncSession,
    record: StudyOutboxRecord,
    authorization: DeliveryAuthorization,
    _provider_idempotency_key: str,
) -> ProviderReceipt:
    if not authorization.allowed:
        raise ProviderOutcomeUnknown
    org, row, user = await _dispatch_context(db, record) or (None, None, None)
    if org is None or row is None or user is None:
        raise ProviderOutcomeUnknown
    origin = await get_org_signup_base_url(org.slug, db_session=db, org_id=org.id)
    if urlsplit(origin).scheme != "https":
        raise ProviderOutcomeUnknown
    href = f"{origin.rstrip('/')}/account/reports?{urlencode({'report': row.uuid})}"
    result = await run_in_threadpool(
        send_email,
        user.email,
        "A progress report is available",
        f'<p><a href="{escape(href, quote=True)}">Sign in to view the progress report</a>.</p>',
        single_attempt=True,
    )
    if isinstance(result, dict):
        reference = result.get("id")
        if isinstance(reference, str) and reference:
            return ProviderReceipt(accepted=True, message_reference=reference)
        # The SMTP adapter confirms acceptance for this recipient without a
        # provider message ID. A missing ID in any other shape is ambiguous.
        if "id" in result and reference is None and result.get("to") == user.email.strip():
            return ProviderReceipt(accepted=True)
    return ProviderReceipt(accepted=False)


async def run_report_delivery_batch(
    db: AsyncSession, *, worker_id: str, limit: int = 25
):
    return await run_delivery_batch(
        db,
        worker_id=worker_id,
        limit=limit,
        purpose=_PURPOSE,
        revalidate=revalidate_report_delivery,
        send=send_report_delivery,
    )
