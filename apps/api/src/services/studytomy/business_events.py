"""Append immutable, privacy-minimised Studytomy decision evidence.

Callers pass the same ``AsyncSession`` that owns the consequential state change.
This function never opens a second connection and never commits; the enclosing
use case is responsible for committing both rows together.
"""

import re
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.studytomy.governance import StudyBusinessEvent

_SAFE_ACTION = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SAFE_REASON = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_SAFE_STATE_KEYS = frozenset(
    {
        "status",
        "policy_key",
        "version",
        "responsibility",
        "scope_kind",
        "capability",
        "is_exclusion",
        "financial_limit_minor",
        "financial_limit_currency",
        "refund_amount_minor",
        "refund_currency",
        "refund_reason_code",
        "refund_first_approval_uuid",
        "refund_access_effect",
        "predecessor_grant_id",
        "review_due_at",
        "retention_cutoff",
        "candidate_count",
        "evidence_reference",
        "evidence_sha256",
        "owner_assignment_id",
        "effective_from",
        "effective_to",
        "decision_code",
        "result_code",
        "resource_kind",
        "policy_version_id",
        "eligibility_kind",
        "control_mode",
        "access_version",
        "authority_version",
        "relationship_type",
        "learner_access_version",
        "requested_scopes",
        "approved_scopes",
        "scope_codes",
        "valid_from",
        "source_mapping_id",
        "source_snapshot_digest",
        "assistance_rule",
        "mastery_policy_uuid",
        "mastery_policy_digest",
        "score_points",
        "maximum_points",
        "evaluator_version",
        "passing_threshold",
        "passed",
        "previous_event_uuid",
        "result_digest",
        "grading_type",
        "result_uuid",
        "submission_content_digest",
        "valid_to",
    }
)
_SAFE_STATE_LIST_KEYS = frozenset({"requested_scopes", "approved_scopes", "scope_codes"})
_SENSITIVE_KEY_PARTS = (
    "password",
    "token",
    "secret",
    "card",
    "cvv",
    "chat",
    "conversation",
    "answer",
    "narrative",
    "email",
    "phone",
    "address",
    "name",
    "attachment",
)


def _safe_state(state: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if state is None:
        return None
    if not isinstance(state, dict) or len(state) > 24:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Business-event state must be a small object of approved metadata fields",
        )
    clean: dict[str, Any] = {}
    for key, value in state.items():
        if (
            not isinstance(key, str)
            or key not in _SAFE_STATE_KEYS
            or any(part in key.casefold() for part in _SENSITIVE_KEY_PARTS)
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Business-event state contains an unapproved or sensitive field",
            )
        if key in _SAFE_STATE_LIST_KEYS:
            if (
                not isinstance(value, list)
                or len(value) > 16
                or any(
                    not isinstance(item, str)
                    or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", item) is None
                    for item in value
                )
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Business-event scopes must be bounded safe codes",
                )
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Business-event state values must be scalar JSON values",
            )
        if isinstance(value, str) and len(value) > 200:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Business-event state text is too long",
            )
        clean[key] = value
    return clean


async def append_business_event(
    db_session: AsyncSession,
    *,
    org_id: int,
    actor_user_id: Optional[int],
    action: str,
    reason_code: str,
    actor_machine_id: Optional[str] = None,
    actor_capacity: Optional[str] = None,
    subject_uuid: Optional[str] = None,
    resource_kind: Optional[str] = None,
    resource_uuid: Optional[str] = None,
    before_state: Optional[dict[str, Any]] = None,
    after_state: Optional[dict[str, Any]] = None,
    policy_refs: Optional[list[int]] = None,
    correlation_id: Optional[str] = None,
    operation_uuid: Optional[str] = None,
    dedupe_key: Optional[str] = None,
) -> StudyBusinessEvent:
    """Add one constrained business event to the caller's active transaction."""
    if (actor_user_id is None) == (not actor_machine_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A business event must identify exactly one human or machine actor",
        )
    if not _SAFE_ACTION.fullmatch(action):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Business-event action must use a stable dotted code",
        )
    if not _SAFE_REASON.fullmatch(reason_code):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Business-event reason must use a stable non-PII reason code",
        )
    if policy_refs is not None and (len(policy_refs) > 20 or any(not isinstance(i, int) or i <= 0 for i in policy_refs)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Business-event policy references must be positive version IDs",
        )

    event = StudyBusinessEvent(
        org_id=org_id,
        actor_user_id=actor_user_id,
        actor_machine_id=actor_machine_id,
        actor_capacity=actor_capacity,
        subject_uuid=subject_uuid,
        resource_kind=resource_kind,
        resource_uuid=resource_uuid,
        action=action,
        before_state=_safe_state(before_state),
        after_state=_safe_state(after_state),
        reason=reason_code,
        policy_refs=policy_refs,
        correlation_id=correlation_id,
        operation_uuid=operation_uuid,
        dedupe_key=dedupe_key,
        created_at=datetime.now(timezone.utc),
    )
    db_session.add(event)
    await db_session.flush()
    return event
