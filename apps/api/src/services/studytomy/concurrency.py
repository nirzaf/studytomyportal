"""Version checks for mutable Studytomy learning drafts.

The same module carries the one canonical lock order (TASK-005). Money, seats and
access can be reached from several domains at once; if each feature locked
whatever it happened to touch, two correct-looking transactions could deadlock or
double-spend. Every Studytomy mutation therefore takes row locks in the single
order below, through this helper, and never with an ad-hoc ``with_for_update()``
at the call site. A Redis day-lock or cache marker is only ever an optimisation on
top: the row lock and the database constraint are what make an invariant hold.
"""

from collections.abc import Sequence
from typing import Any, Optional, Type

from fastapi import HTTPException
from sqlalchemy import select
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from src.services.studytomy.errors import STALE_VERSION, studytomy_http_error


def require_expected_version(
    resource: str,
    expected_version: int | None,
    current_version: int,
) -> None:
    """Reject a write based on a stale version while the caller holds its row lock."""
    if expected_version is None:
        return
    if expected_version != current_version:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{resource} changed since it was read; current version is "
                f"{current_version} (STALE_WRITE). Reload the resource before saving."
            ),
        )


# Spec §7.1. Seats before money, money before the access it pays for.
LOCK_ORDER: tuple[str, ...] = (
    "capacity",
    "order_line",
    "membership_cycle",
    "collection",
    "refund_reservation",
    "entitlement",
)

_LOCK_TARGETS: dict[str, Type[SQLModel]] = {}


class LockOrderError(RuntimeError):
    """A caller asked for locks in a shape that cannot be serialized safely."""


def register_lock_target(kind: str, model: Type[SQLModel]) -> None:
    """Bind a lock kind to its table. Called once by the owning domain module."""
    if kind not in LOCK_ORDER:
        raise LockOrderError(f"Unknown lock kind {kind!r}")
    existing = _LOCK_TARGETS.get(kind)
    if existing is not None and existing is not model:
        raise LockOrderError(f"Lock kind {kind!r} is already bound to {existing.__name__}")
    _LOCK_TARGETS[kind] = model


def lock_rank(kind: str) -> int:
    try:
        return LOCK_ORDER.index(kind)
    except ValueError as error:  # pragma: no cover - programming mistake
        raise LockOrderError(f"Unknown lock kind {kind!r}") from error


def _validate_order(requested: Sequence[tuple[str, int]]) -> None:
    seen: Optional[int] = None
    seen_kind: Optional[str] = None
    for kind, row_id in requested:
        if not isinstance(row_id, int) or row_id <= 0:
            raise LockOrderError(f"Lock target {kind!r} needs a positive row id")
        rank = lock_rank(kind)
        if seen is not None and rank < seen:
            raise LockOrderError(
                f"Lock order violation: {kind} follows {seen_kind}; "
                f"acquire locks in the order {', '.join(LOCK_ORDER)}"
            )
        seen, seen_kind = rank, kind


async def acquire_write_locks(
    db_session: AsyncSession,
    requested: Sequence[tuple[str, int]],
) -> None:
    """Lock ``(kind, id)`` rows in the canonical order supplied by the caller.

    Rows of one kind are locked together in ascending id order so two concurrent
    transactions that touch overlapping sets cannot cross. The caller must never
    re-sort on our behalf: an out-of-order request is a bug and is raised.
    """
    if not requested:
        return
    _validate_order(requested)

    grouped: list[tuple[str, list[int]]] = []
    for kind, row_id in requested:
        if grouped and grouped[-1][0] == kind:
            grouped[-1][1].append(row_id)
        else:
            grouped.append((kind, [row_id]))

    for kind, row_ids in grouped:
        model = _LOCK_TARGETS.get(kind)
        if model is None:
            raise LockOrderError(f"Lock kind {kind!r} has no registered table")
        unique_ids = sorted(set(row_ids))
        result = await db_session.execute(
            select(model.id)  # type: ignore[attr-defined]
            .where(model.id.in_(unique_ids))  # type: ignore[attr-defined]
            .order_by(model.id)  # type: ignore[attr-defined]
            .with_for_update()
        )
        locked = list(result.scalars().all())
        if len(locked) != len(unique_ids):
            raise studytomy_http_error(
                409,
                STALE_VERSION,
                "A record required for this operation no longer exists.",
                next_action="Reload the resource and repeat the operation.",
            )


def require_version_match(
    actual_version: int,
    expected_version: Optional[int],
    *,
    resource_label: str = "record",
) -> None:
    """Reject a write to a governed authority row built on a stale read.

    This is the Studytomy contract (a coded ``409 STALE_VERSION``); the plain
    ``require_expected_version`` above stays as the editor/draft callers already
    report to their clients.
    """
    if expected_version is None:
        raise studytomy_http_error(
            422,
            STALE_VERSION,
            f"expected_version is required to modify this {resource_label}.",
            field_errors={"expected_version": "Required"},
        )
    if int(actual_version) != int(expected_version):
        raise studytomy_http_error(
            409,
            STALE_VERSION,
            f"This {resource_label} has changed since you read it.",
            next_action="Reload the current version and reapply your change.",
        )


def bump_version(current_version: int) -> int:
    """Return the next row version; callers persist it inside the same transaction."""
    version = int(current_version)
    if version < 1:
        raise LockOrderError("Row versions start at 1")
    return version + 1


def safe_replay_projection(record: Any, fields: Sequence[str]) -> dict:
    """Copy only the named fields of a committed resource into a replay response.

    Response projections are stored as JSON, so an accidental inclusion here
    would persist private content. Whitelisting is deliberate, not convenience.
    """
    return {field: getattr(record, field, None) for field in fields}
