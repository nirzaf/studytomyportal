"""One error shape for every Studytomy endpoint.

Existing LearnHouse endpoints keep their current detail shapes; nothing here is
applied globally. A Studytomy failure always carries a stable machine-readable
``code`` so a client can branch on the reason instead of parsing prose, and a
safe ``next_action`` where the source rules require one.
"""

from typing import Any, Optional

from fastapi import HTTPException


def studytomy_error_body(
    code: str,
    message: str,
    *,
    correlation_id: Optional[str] = None,
    field_errors: Optional[dict[str, str]] = None,
    next_action: Optional[str] = None,
) -> dict[str, Any]:
    """Build the response detail. Only a real correlation id is emitted."""
    body: dict[str, Any] = {"code": code, "message": message}
    if correlation_id:
        body["correlation_id"] = correlation_id
    if field_errors:
        body["field_errors"] = dict(field_errors)
    if next_action:
        body["next_action"] = next_action
    return body


def studytomy_http_error(
    status_code: int,
    code: str,
    message: str,
    *,
    correlation_id: Optional[str] = None,
    field_errors: Optional[dict[str, str]] = None,
    next_action: Optional[str] = None,
) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=studytomy_error_body(
            code,
            message,
            correlation_id=correlation_id,
            field_errors=field_errors,
            next_action=next_action,
        ),
    )


# Locked codes shared across the Studytomy backlog. Distinct denial reasons stay
# distinct so a client never has to guess why it was refused.
STALE_VERSION = "STALE_VERSION"
MANAGED_WORKFLOW_REQUIRED = "MANAGED_WORKFLOW_REQUIRED"
POLICY_NOT_APPROVED = "POLICY_NOT_APPROVED"
IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
CAPACITY_EXHAUSTED = "CAPACITY_EXHAUSTED"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
