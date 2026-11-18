"""Internal projections of retained evidence for pilot reconciliation."""

from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.studytomy.cases import StudyServiceCase


async def current_support_backlog(
    db: AsyncSession, *, org_id: int,
) -> dict[str, datetime | int | None]:
    """Summarize unresolved support cases for an already authorized organization.

    This is a current snapshot with elapsed calendar time. Service-hour targets
    and safeguarding cases are outside the measure.
    """
    if org_id < 1:
        raise ValueError("A valid organization is required.")
    observed_at = datetime.now(UTC)
    count, oldest = (await db.execute(
        select(func.count(), func.min(StudyServiceCase.created_at)).where(
            StudyServiceCase.org_id == org_id,
            StudyServiceCase.category == "support",
            StudyServiceCase.status.in_(("acknowledged", "assigned", "awaiting_information", "under_review")),
            StudyServiceCase.created_at <= observed_at,
        )
    )).one()
    return {
        "observed_at": observed_at,
        "unresolved_cases": count,
        "oldest_unresolved_age_seconds": (
            int((observed_at - oldest).total_seconds()) if oldest is not None else None
        ),
    }


async def assessment_evidence_breakdown(
    db: AsyncSession, *, org_id: int, curriculum_revision_id: int,
    start: datetime, end: datetime,
) -> list[dict[str, int | str]]:
    """Count current evidence heads by assistance and outcome for one revision.

    The caller must authorize access to the requested scope. Entries count
    logical observations, not learners, comparable pairs, or mastery gains.
    """
    if (
        org_id < 1 or curriculum_revision_id < 1
        or start.utcoffset() is None or end.utcoffset() is None or start >= end
    ):
        raise ValueError("An organization, curriculum revision and ordered, timezone-aware period are required.")
    result = await db.execute(text("""
        WITH ranked AS (
            SELECT observed_at, independence, outcome, is_retraction,
                   row_number() OVER (
                       PARTITION BY learner_profile_id, concept_id, logical_source_uuid
                       ORDER BY result_revision DESC, evidence_sequence DESC
                   ) AS position
            FROM st_learning_evidence
            WHERE org_id = :org_id AND curriculum_revision_id = :revision
        )
        SELECT independence,
               CASE WHEN is_retraction THEN 'retracted' ELSE outcome END AS evidence_outcome,
               count(*) AS observations
        FROM ranked
        WHERE position = 1 AND observed_at >= :start AND observed_at < :end
        GROUP BY independence, evidence_outcome
        ORDER BY independence, evidence_outcome
    """), {"org_id": org_id, "revision": curriculum_revision_id, "start": start, "end": end})
    return [dict(row) for row in result.mappings()]


async def confirmed_cash_by_currency(
    db: AsyncSession, *, org_id: int, start: datetime, end: datetime,
    offer_uuid: str | None = None,
) -> list[dict[str, int | str]]:
    """Summarize amounts and ledger entry counts in the half-open period.

    The caller must authorize finance access for the organization and, when
    supplied, the exact offer. Collections are
    limited to movements with retained manual settlement evidence. An empty
    result means no supported confirmed movements are in scope. Counts refer
    to ledger entries, so multiple refunds may belong to one paid order.
    Tax and earned revenue are outside this projection.
    An offer filter includes orders whose lines all belong to that offer.
    Mixed-offer orders require an allocation rule before offer attribution.
    """
    if org_id < 1 or start.utcoffset() is None or end.utcoffset() is None or start >= end:
        raise ValueError("An organization and an ordered, timezone-aware period are required.")
    result = await db.execute(text("""
        WITH offer_orders AS (
            SELECT l.order_id
            FROM st_order_line l
            JOIN st_offer o ON o.org_id = l.org_id AND o.id = l.offer_id
            WHERE l.org_id = :org_id
            GROUP BY l.order_id
            HAVING bool_and(o.offer_uuid = CAST(:offer_uuid AS text))
        ), cash AS (
            SELECT m.order_id, m.currency, m.amount_minor, 'collection' AS kind, e.settled_at AS effective_at
            FROM st_money_movement m
            JOIN st_manual_collection_evidence e
              ON e.org_id = m.org_id AND e.order_id = m.order_id AND e.attempt_id = m.attempt_id
            WHERE m.org_id = :org_id
            UNION ALL
            SELECT r.order_id, r.currency, r.amount_minor, 'refund', s.settled_at
            FROM st_refund_settlement s
            JOIN st_refund_reservation r ON r.org_id = s.org_id AND r.id = s.reservation_id
            WHERE s.org_id = :org_id
            UNION ALL
            SELECT order_id, currency, amount_minor, 'chargeback', effective_at
            FROM st_chargeback_adjustment WHERE org_id = :org_id
            UNION ALL
            SELECT a.order_id, a.currency, a.amount_minor, 'reversal', r.effective_at
            FROM st_chargeback_reversal r
            JOIN st_chargeback_adjustment a ON a.org_id = r.org_id AND a.id = r.adjustment_id
            WHERE r.org_id = :org_id
        )
        SELECT currency,
               count(*) FILTER (WHERE kind = 'collection') AS collection_count,
               count(*) FILTER (WHERE kind = 'refund') AS settled_refund_count,
               count(*) FILTER (WHERE kind = 'chargeback') AS chargeback_count,
               count(*) FILTER (WHERE kind = 'reversal') AS chargeback_reversal_count,
               sum(CASE WHEN kind = 'collection' THEN amount_minor ELSE 0 END) AS collected_minor,
               sum(CASE WHEN kind = 'refund' THEN amount_minor ELSE 0 END) AS refunded_minor,
               sum(CASE WHEN kind = 'chargeback' THEN amount_minor ELSE 0 END) AS chargeback_minor,
               sum(CASE WHEN kind = 'reversal' THEN amount_minor ELSE 0 END) AS reversed_chargeback_minor,
               sum(CASE WHEN kind IN ('refund', 'chargeback') THEN -amount_minor ELSE amount_minor END) AS net_minor
        FROM cash
        WHERE effective_at >= :start AND effective_at < :end
          AND (CAST(:offer_uuid AS text) IS NULL OR order_id IN (SELECT order_id FROM offer_orders))
        GROUP BY currency ORDER BY currency
    """), {"org_id": org_id, "start": start, "end": end, "offer_uuid": offer_uuid})
    return [
        {key: value if key == "currency" else int(value) for key, value in row.items()}
        for row in result.mappings()
    ]
