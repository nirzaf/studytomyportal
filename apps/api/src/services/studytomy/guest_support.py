"""Purpose-scoped guest support verification and maintenance."""

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import delete
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from config.config import get_learnhouse_config
from src.db.organizations import Organization
from src.db.studytomy.cases import StudyGuestSupportChallenge, StudyServiceCase
from src.db.studytomy.governance import StudyPolicyVersion
from src.services.email.utils import send_email
from src.services.studytomy.business_events import append_business_event
from src.services.studytomy.policies import lock_organization
from src.services.studytomy.support import SupportCaseReceipt, _guest_verification_available, _support_guidance

_CHALLENGE_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
logger = logging.getLogger(__name__)


def _contact_key() -> bytes:
    secret = get_learnhouse_config().security_config.auth_jwt_secret_key
    return hmac.digest(secret.encode(), b"studytomy:guest-support-contact:v1", "sha256")


def guest_contact_bucket(email: str) -> str:
    return hmac.digest(_contact_key(), email.casefold().encode(), "sha256").hex()


async def issue_guest_support_challenge(db: AsyncSession, org: Organization, email: str, topic: str) -> str:
    unavailable = HTTPException(404, "Guest support verification is unavailable.")
    if os.getenv("LEARNHOUSE_STUDYTOMY_GUEST_SUPPORT_ENABLED") != "true":
        raise unavailable
    await lock_organization(db, org.id)
    now = datetime.now(UTC)
    guidance, policy_ids, _ = await _support_guidance(db, org)
    service = await db.get(StudyPolicyVersion, policy_ids[0])
    if (
        not guidance.intake_available or topic not in guidance.categories or service is None
        or not await _guest_verification_available(db, org, service, now)
    ):
        raise unavailable
    token = secrets.token_urlsafe(32)
    db.add(StudyGuestSupportChallenge(
        org_id=org.id, token_digest=hashlib.sha256(token.encode("ascii")).hexdigest(),
        contact_ciphertext=Fernet(base64.urlsafe_b64encode(_contact_key())).encrypt(email.encode()).decode(),
        topic=topic, created_at=now, expires_at=now + timedelta(minutes=10),
    ))
    return token


def deliver_guest_support_challenge(email: str, token: str) -> None:
    try:
        send_email(
            email, "Studytomy support verification",
            f"<p>Your support verification code is <strong>{token}</strong>.</p>"
            "<p>Enter this code on the help page within 10 minutes. "
            "If you did not request it, you can ignore this message.</p>",
            single_attempt=True,
        )
    except Exception:
        logger.warning("Guest support verification email could not be delivered")


async def redeem_guest_support_challenge(
    db: AsyncSession, org: Organization, token: str,
) -> SupportCaseReceipt:
    """Use a delivered proof once, keeping the original receipt retryable until expiry."""
    unavailable = HTTPException(404, "Guest support verification is unavailable.")
    if os.getenv("LEARNHOUSE_STUDYTOMY_GUEST_SUPPORT_ENABLED") != "true" or not _CHALLENGE_TOKEN.fullmatch(token):
        raise unavailable
    await lock_organization(db, org.id)
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    challenge = (await db.exec(select(StudyGuestSupportChallenge).where(
        StudyGuestSupportChallenge.org_id == org.id,
        StudyGuestSupportChallenge.token_digest == digest,
    ).with_for_update().execution_options(populate_existing=True))).one_or_none()
    now = datetime.now(UTC)
    if challenge is None or challenge.expires_at <= now or challenge.created_at > now:
        raise unavailable
    guidance, policy_ids, staff = await _support_guidance(db, org)
    policy = await db.get(StudyPolicyVersion, policy_ids[0])
    if policy is None or not await _guest_verification_available(db, org, policy, now):
        raise unavailable
    if challenge.case_uuid is not None:
        return SupportCaseReceipt(case_uuid=UUID(challenge.case_uuid), topic=challenge.topic, status="assigned")
    if not guidance.intake_available or challenge.topic not in guidance.categories:
        raise unavailable

    case = StudyServiceCase(
        org_id=org.id, category="support", support_topic=challenge.topic,
        guest_contact_verified_at=now, owner_user_id=staff[0], status="assigned",
    )
    db.add(case)
    await db.flush()
    challenge.consumed_at = now
    challenge.case_uuid = case.case_uuid
    await append_business_event(
        db, org_id=org.id, actor_user_id=None, actor_machine_id="guest-support-verifier",
        actor_capacity="contact_verification", resource_kind="case", resource_uuid=case.case_uuid,
        action="studytomy.support.redeem_guest_challenge", reason_code="GUEST_SUPPORT_CONTACT_VERIFIED",
        after_state={"status": case.status, "case_owner_user_id": staff[0]}, policy_refs=policy_ids,
    )
    return SupportCaseReceipt(case_uuid=UUID(case.case_uuid), topic=challenge.topic, status=case.status)


async def purge_expired_guest_support_challenges(
    db: AsyncSession, *, org_id: int, limit: int, execute: bool = False,
    now: datetime | None = None,
) -> int:
    """Return the size of one expired batch. The caller owns the transaction."""
    if not 1 <= limit <= 1000:
        raise ValueError("Guest support purge limit must be between 1 and 1000")
    cutoff = now if now is not None else datetime.now(UTC)
    if cutoff.utcoffset() is None:
        raise ValueError("Guest support purge requires a timezone-aware cutoff")
    challenge = StudyGuestSupportChallenge
    batch = select(challenge.id).where(
        challenge.org_id == org_id, challenge.expires_at <= cutoff,
    ).order_by(challenge.expires_at, challenge.id).limit(limit)
    if not execute:
        return len((await db.exec(batch)).all())
    result = await db.execute(
        delete(challenge).where(challenge.id.in_(batch.with_for_update(skip_locked=True)))
        .returning(challenge.id)
    )
    return len(result.scalars().all())
