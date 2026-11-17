"""Durable tutor turns with a single dispatch per logical operation."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException, Request
from httpx import TransportError
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from pydantic_ai.exceptions import ModelAPIError
from redis.exceptions import RedisError
from sqlalchemy import func, select, update
from sqlmodel.ext.asyncio.session import AsyncSession

from config.config import get_learnhouse_config
from src.db.organizations import Organization
from src.db.studytomy.adaptive_guidance import StudyTutorSession, StudyTutorTurn
from src.db.studytomy.governance import StudyPolicyKey
from src.db.users import PublicUser
from src.security.features_utils.usage import reserve_ai_credit
from src.services.studytomy.adaptive_diagnostics import _policy_body
from src.services.studytomy.adaptive_guidance import load_guidance_context
from src.services.studytomy.adaptive_teaching import (
    TeachingResult,
    apply_reviewed_hint,
    generate_teaching,
    reviewed_explanation,
)
from src.services.studytomy.errors import (
    POLICY_NOT_APPROVED,
    STALE_VERSION,
    studytomy_http_error,
)
from src.services.studytomy.idempotency import canonical_request_hash
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.policies import _as_utc

TurnState = Literal[
    "running", "completed", "failed", "reconciliation_required", "cancelled"
]
_DISPATCH_WINDOW = timedelta(minutes=2)


class TutorTurnCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_uuid: UUID
    expected_version: Annotated[int, Field(strict=True, ge=1)]
    action: Literal["explain", "hint", "check_understanding"]
    message: Annotated[str, Field(min_length=1, max_length=500)] | None = None


class TutorTurnRead(BaseModel):
    operation_uuid: UUID
    state: TurnState
    session_version: int
    result: TeachingResult | None = None


FeedbackIssue = Literal["inaccurate", "unclear", "unsafe"]


class TutorFeedbackCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_uuid: UUID
    issue_code: FeedbackIssue
    note: (
        Annotated[
            str,
            StringConstraints(
                strip_whitespace=True,
                min_length=1,
                max_length=300,
                pattern=r"^[^\x00-\x1f\x7f]+$",
            ),
        ]
        | None
    ) = None


class TutorFeedbackReceipt(BaseModel):
    turn_uuid: UUID
    issue_code: FeedbackIssue
    received_at: datetime


class _TurnLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    max_turns_per_day: Annotated[int, Field(ge=1, le=1000)]


class _TurnRetention(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    result_hours: Annotated[int, Field(ge=1, le=720)]
    feedback_notes: bool = False


class _ProviderApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: Literal[True]
    provider: Annotated[str, Field(min_length=1, max_length=32)]
    model: Annotated[str, Field(min_length=1, max_length=120)]
    base_url: Annotated[str, Field(min_length=1, max_length=300)] | None = None


def _guidance_rules(body, area: str):
    value = body.specified_value(area)
    return value.get("adaptive_guidance") if isinstance(value, dict) else None


async def _turn_policy(db: AsyncSession, org: Organization, now: datetime):
    policies = await _current_policies(
        db, org, (StudyPolicyKey.D_10, StudyPolicyKey.D_13), now
    )
    d10 = _policy_body(policies, StudyPolicyKey.D_10)
    d13 = _policy_body(policies, StudyPolicyKey.D_13)
    try:
        limits = _TurnLimits.model_validate(_guidance_rules(d10, "usage_limits"))
        retention = _TurnRetention.model_validate(_guidance_rules(d13, "retention"))
    except ValidationError as error:
        raise studytomy_http_error(
            409,
            POLICY_NOT_APPROVED,
            "Reviewed guidance limits and retention are required.",
        ) from error
    try:
        approval = _ProviderApproval.model_validate(_guidance_rules(d10, "review"))
    except ValidationError:
        approval = None
    model = None
    if approval:
        config = get_learnhouse_config().ai_config
        if (
            config.provider == approval.provider
            and config.base_url == approval.base_url
        ):
            model = approval.model
    return limits.max_turns_per_day, timedelta(hours=retention.result_hours), model


async def _maintain_turns(
    db: AsyncSession, org_id: int, learner_id: int, now: datetime
) -> None:
    sessions = select(StudyTutorSession.id).where(
        StudyTutorSession.org_id == org_id,
        StudyTutorSession.learner_profile_id == learner_id,
    )
    rows = (StudyTutorTurn.org_id == org_id, StudyTutorTurn.session_id.in_(sessions))
    await db.execute(
        update(StudyTutorTurn)
        .where(
            *rows,
            StudyTutorTurn.state == "running",
            StudyTutorTurn.created_at <= now - _DISPATCH_WINDOW,
        )
        .values(state="reconciliation_required", finished_at=now)
    )
    await db.execute(
        update(StudyTutorTurn)
        .where(
            *rows,
            StudyTutorTurn.result.is_not(None),
            StudyTutorTurn.result_expires_at <= now,
        )
        .values(result=None, feedback_note=None)
    )


async def _find_turn(
    db: AsyncSession, org_id: int, session_id: int, operation_uuid: UUID
):
    return (
        await db.execute(
            select(StudyTutorTurn)
            .where(
                StudyTutorTurn.org_id == org_id,
                StudyTutorTurn.session_id == session_id,
                StudyTutorTurn.operation_uuid == str(operation_uuid),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


def _read(turn: StudyTutorTurn) -> TutorTurnRead:
    version = turn.requested_version + int(turn.state == "completed")
    return TutorTurnRead(
        operation_uuid=turn.operation_uuid,
        state=turn.state,
        session_version=version,
        result=TeachingResult.model_validate(turn.result)
        if turn.result is not None
        and _as_utc(turn.result_expires_at) > datetime.now(UTC)
        else None,
    )


def _complete(
    session: StudyTutorSession,
    turn: StudyTutorTurn,
    result: TeachingResult,
    now: datetime,
):
    turn.state = "completed"
    turn.finished_at = now
    turn.result = result.model_dump(mode="json")
    session.step += 1
    session.version += 1


async def read_tutor_turn(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    session_uuid: str,
    operation_uuid: UUID,
) -> TutorTurnRead:
    session, _ = await load_guidance_context(request, db, org, actor, session_uuid)
    await _maintain_turns(db, org.id, session.learner_profile_id, datetime.now(UTC))
    turn = await _find_turn(db, org.id, session.id, operation_uuid)
    if turn is None:
        raise HTTPException(404, "Guidance turn not found.")
    response = _read(turn)
    await db.commit()
    return response


async def create_tutor_turn(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    session_uuid: str,
    body: TutorTurnCreate,
) -> TutorTurnRead:
    session, context = await load_guidance_context(
        request, db, org, actor, session_uuid
    )
    now = datetime.now(UTC)
    await _maintain_turns(db, org.id, session.learner_profile_id, now)
    digest = canonical_request_hash(body.model_dump(mode="json"))
    turn = await _find_turn(db, org.id, session.id, body.operation_uuid)
    if turn is not None:
        if turn.request_digest != digest:
            raise studytomy_http_error(
                409,
                "IDEMPOTENCY_CONFLICT",
                "The operation already has a different request.",
            )
        response = _read(turn)
        await db.commit()
        return response
    if session.version != body.expected_version:
        raise studytomy_http_error(409, STALE_VERSION, "Reload the guidance session.")
    running = (
        await db.execute(
            select(StudyTutorTurn.id)
            .where(
                StudyTutorTurn.org_id == org.id,
                StudyTutorTurn.session_id == session.id,
                StudyTutorTurn.state == "running",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if running is not None:
        raise studytomy_http_error(
            409, "GUIDANCE_TURN_RUNNING", "Recover the current guidance turn first."
        )
    limit, retention, model = await _turn_policy(db, org, now)
    used = (
        await db.execute(
            select(func.count(StudyTutorTurn.id))
            .join(StudyTutorSession, StudyTutorSession.id == StudyTutorTurn.session_id)
            .where(
                StudyTutorTurn.org_id == org.id,
                StudyTutorSession.org_id == org.id,
                StudyTutorSession.learner_profile_id == session.learner_profile_id,
                StudyTutorTurn.created_at >= now - timedelta(days=1),
            )
        )
    ).scalar_one()
    if used >= limit:
        raise HTTPException(429, "The approved daily guidance limit has been reached.")
    turn = StudyTutorTurn(
        org_id=org.id,
        session_id=session.id,
        operation_uuid=str(body.operation_uuid),
        action=body.action,
        request_digest=digest,
        requested_version=body.expected_version,
        state="running",
        result_expires_at=now + retention,
        created_at=now,
    )
    db.add(turn)
    if body.action == "hint" or context.practice_linked or model is None:
        if body.action == "hint":
            result = await apply_reviewed_hint(request, db, org, actor, session_uuid)
        elif context.practice_linked:
            raise HTTPException(409, "Use the reviewed hint for supported practice.")
        else:
            result = reviewed_explanation(context)
        _complete(session, turn, result, now)
        response = _read(turn)
        await db.commit()
        return response

    org_id, session_id = org.id, session.id
    await db.commit()  # The durable marker precedes cross-store or provider calls.
    try:
        credit_used = await reserve_ai_credit(org_id, db)
    except HTTPException as error:
        if error.status_code not in (403, 404, 429):
            return await _mark_uncertain(db, org_id, session_id, body.operation_uuid)
        # A known quota or feature denial leaves no provider call to reconcile.
        return await _finish(
            request, db, org, actor, session_uuid, session_id, body.operation_uuid, None
        )
    except TimeoutError, ConnectionError, RedisError:
        await db.rollback()
        return await _mark_uncertain(db, org_id, session_id, body.operation_uuid)

    turn = await _find_turn(db, org_id, session_id, body.operation_uuid)
    if turn.state != "running":
        response = _read(turn)
        await db.commit()
        return response
    turn.credit_used = credit_used
    await db.commit()

    try:
        current, context = await load_guidance_context(
            request, db, org, actor, session_uuid
        )
        if current.version != body.expected_version:
            raise HTTPException(409, "The guidance session changed.")
    except HTTPException:
        await db.rollback()
        return await _mark_uncertain(db, org_id, session_id, body.operation_uuid)
    await db.commit()

    try:
        result = await generate_teaching(context, action=body.action, model_name=model)
    except TimeoutError, ConnectionError, TransportError, ModelAPIError:
        return await _mark_uncertain(db, org_id, session_id, body.operation_uuid)
    return await _finish(
        request, db, org, actor, session_uuid, session_id, body.operation_uuid, result
    )


async def _mark_uncertain(
    db: AsyncSession, org_id: int, session_id: int, operation_uuid: UUID
):
    turn = await _find_turn(db, org_id, session_id, operation_uuid)
    if turn.state == "running":
        turn.state = "reconciliation_required"
        turn.finished_at = datetime.now(UTC)
    response = _read(turn)
    await db.commit()
    return response


async def _finish(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    session_uuid: str,
    session_id: int,
    operation_uuid: UUID,
    result: TeachingResult | None,
):
    org_id = org.id
    try:
        session, context = await load_guidance_context(
            request, db, org, actor, session_uuid
        )
    except HTTPException:
        await db.rollback()
        return await _mark_uncertain(db, org_id, session_id, operation_uuid)
    turn = await _find_turn(db, org_id, session_id, operation_uuid)
    now = datetime.now(UTC)
    if turn.state == "running":
        if (
            session.version != turn.requested_version
            or _as_utc(turn.created_at) <= now - _DISPATCH_WINDOW
            or _as_utc(turn.result_expires_at) <= now
        ):
            turn.state = "reconciliation_required"
            turn.finished_at = now
        else:
            _complete(session, turn, result or reviewed_explanation(context), now)
    response = _read(turn)
    await db.commit()
    return response


async def submit_tutor_feedback(
    request: Request,
    db: AsyncSession,
    org: Organization,
    actor: PublicUser,
    session_uuid: str,
    body: TutorFeedbackCreate,
) -> TutorFeedbackReceipt:
    session, _ = await load_guidance_context(request, db, org, actor, session_uuid)
    now = datetime.now(UTC)
    turn = await _find_turn(db, org.id, session.id, body.turn_uuid)
    if turn is None or turn.state != "completed":
        raise HTTPException(404, "Guidance turn not found.")
    if _as_utc(turn.result_expires_at) <= now:
        if turn.result is not None or turn.feedback_note is not None:
            turn.result = None
            turn.feedback_note = None
            await db.commit()
        raise HTTPException(404, "Guidance turn not found.")
    if turn.result is None:
        raise HTTPException(404, "Guidance turn not found.")
    if body.note is not None:
        policies = await _current_policies(db, org, (StudyPolicyKey.D_13,), now)
        rule = _guidance_rules(_policy_body(policies, StudyPolicyKey.D_13), "retention")
        if not isinstance(rule, dict) or rule.get("feedback_notes") is not True:
            raise studytomy_http_error(
                409, POLICY_NOT_APPROVED, "Feedback notes are unavailable."
            )
    if turn.feedback_at is None:
        turn.feedback_issue = body.issue_code
        turn.feedback_note = body.note
        turn.feedback_at = now
    elif (turn.feedback_issue, turn.feedback_note) != (body.issue_code, body.note):
        raise studytomy_http_error(
            409, "IDEMPOTENCY_CONFLICT", "Feedback was already recorded for this turn."
        )
    receipt = TutorFeedbackReceipt(
        turn_uuid=body.turn_uuid,
        issue_code=body.issue_code,
        received_at=_as_utc(turn.feedback_at),
    )
    await db.commit()
    return receipt
