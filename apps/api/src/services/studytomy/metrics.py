"""Internal projections of retained evidence for pilot reconciliation."""

from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlmodel.ext.asyncio.session import AsyncSession

from src.db.organizations import Organization
from src.db.studytomy.cases import StudyServiceCase
from src.db.studytomy.commerce import StudyOffer, StudyOfferVersion
from src.db.studytomy.governance import StudyPolicyKey
from src.db.users import PublicUser
from src.security.studytomy_access import (
    StudytomyCapabilityName,
    authorize_studytomy_staff_action,
    evaluate_studytomy_staff_access,
)
from src.services.studytomy.learners import _current_policies
from src.services.studytomy.orders import _pilot_offer_versions, _require_pilot_offer

_CASH_REPORT_POLICIES = (StudyPolicyKey.D_15, StudyPolicyKey.D_17, StudyPolicyKey.D_19)


class PilotCashOffer(BaseModel):
    offer_uuid: str
    offer_version_uuid: str
    title: str
    version: int


async def list_pilot_cash_offers(
    db: AsyncSession, org: Organization, actor: PublicUser,
) -> list[PilotCashOffer]:
    now = datetime.now(UTC)
    _, pilot, _ = await _current_policies(db, org, _CASH_REPORT_POLICIES, now)
    versions = _pilot_offer_versions(pilot)
    rows = (await db.execute(
        select(StudyOffer.offer_uuid, StudyOfferVersion)
        .join(StudyOfferVersion, StudyOfferVersion.offer_id == StudyOffer.id)
        .where(
            StudyOffer.org_id == org.id,
            StudyOffer.offer_uuid.in_(versions),
            StudyOfferVersion.version_uuid.in_([value for value in versions.values() if isinstance(value, str)]),
        )
        .order_by(StudyOffer.offer_uuid, StudyOfferVersion.version_no)
    )).all()
    options = []
    for offer_uuid, version in rows:
        if versions.get(offer_uuid) != version.version_uuid:
            continue
        access = await evaluate_studytomy_staff_access(
            db, actor, org, StudytomyCapabilityName.FINANCIAL_RECONCILIATION,
            target_scope_kind="offer", target_resource_uuid=offer_uuid, now=now,
        )
        if access.allowed:
            title = version.terms.get("title")
            options.append(PilotCashOffer(
                offer_uuid=offer_uuid, offer_version_uuid=version.version_uuid,
                title=title if isinstance(title, str) and title.strip() else "Course offer",
                version=version.version_no,
            ))
    return options


class SettledRefundReason(BaseModel):
    currency: str
    reason_code: str
    settlement_count: int
    refunded_minor: int


class PilotCashReport(BaseModel):
    org_uuid: str
    offer_uuid: str
    offer_version_uuid: str
    period_start: datetime
    period_end: datetime
    observed_at: datetime
    policy_version_ids: list[int]
    query_version: str = "confirmed_cash_v2"
    population: str = "Orders with all lines belonging to the selected offer version."
    exclusions: str = "Unconfirmed movements, mixed-offer and mixed-version orders, and tax and revenue calculations."
    confirmed_cash: list[dict[str, int | str]]
    settled_refunds_by_reason: list[SettledRefundReason]


async def read_pilot_cash_report(
    db: AsyncSession, org: Organization, actor: PublicUser, *,
    offer_uuid: str, offer_version_uuid: str, start: datetime, end: datetime,
) -> PilotCashReport:
    observed_at = datetime.now(UTC)
    if start >= end or end > observed_at:
        raise HTTPException(422, "Use an ordered reporting period ending at or before the current time.")
    await authorize_studytomy_staff_action(
        db, actor, org, StudytomyCapabilityName.FINANCIAL_RECONCILIATION,
        target_scope_kind="offer", target_resource_uuid=offer_uuid,
    )
    policies = await _current_policies(
        db, org, _CASH_REPORT_POLICIES, observed_at,
    )
    pilot = next(policy for policy in policies if policy.policy_key == StudyPolicyKey.D_17.value)
    _require_pilot_offer(pilot, offer_uuid, offer_version_uuid)
    totals, reasons = await _confirmed_cash_rollup(
        db, org_id=org.id, offer_uuid=offer_uuid, offer_version_uuid=offer_version_uuid,
        start=start, end=end,
    )
    return PilotCashReport(
        org_uuid=org.org_uuid, offer_uuid=offer_uuid, offer_version_uuid=offer_version_uuid,
        period_start=start, period_end=end, observed_at=observed_at,
        policy_version_ids=[policy.id for policy in policies],
        confirmed_cash=totals,
        settled_refunds_by_reason=reasons,
    )


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
    course_uuid: str | None = None, release_uuid: str | None = None,
) -> list[dict[str, int | str]]:
    """Count current evidence heads by assistance and outcome for one revision.

    The caller must authorize access to the requested scope. Release filters
    use the frozen attempt binding. Entries count
    logical observations, not learners, comparable pairs, or mastery gains.
    Mastery-eligible counts use the evidence journal's recorded eligibility.
    """
    if (
        org_id < 1 or curriculum_revision_id < 1
        or start.utcoffset() is None or end.utcoffset() is None or start >= end
        or (release_uuid is not None and not course_uuid)
    ):
        raise ValueError("An organization, curriculum revision and ordered, timezone-aware period are required.")
    result = await db.execute(text("""
        WITH ranked AS (
            SELECT e.observed_at, e.independence, e.outcome, e.is_retraction, e.eligible,
                   row_number() OVER (
                       PARTITION BY e.learner_profile_id, e.concept_id, e.logical_source_uuid
                       ORDER BY e.result_revision DESC, e.evidence_sequence DESC
                   ) AS position
            FROM st_learning_evidence e
            JOIN st_attempt_item i ON i.org_id = e.org_id AND i.id = e.attempt_item_id
            JOIN st_learning_attempt a ON a.org_id = i.org_id AND a.id = i.attempt_id
            WHERE e.org_id = :org_id AND e.curriculum_revision_id = :revision
              AND (CAST(:course_uuid AS text) IS NULL OR a.source_course_uuid = CAST(:course_uuid AS text))
              AND (CAST(:release_uuid AS text) IS NULL OR a.source_release_uuid = CAST(:release_uuid AS text))
        )
        SELECT independence,
               CASE WHEN is_retraction THEN 'retracted' ELSE outcome END AS evidence_outcome,
               count(*) AS observations,
               count(*) FILTER (WHERE eligible AND NOT is_retraction) AS mastery_eligible_observations
        FROM ranked
        WHERE position = 1 AND observed_at >= :start AND observed_at < :end
        GROUP BY independence, evidence_outcome
        ORDER BY independence, evidence_outcome
    """), {
        "org_id": org_id, "revision": curriculum_revision_id, "start": start, "end": end,
        "course_uuid": course_uuid, "release_uuid": release_uuid,
    })
    return [dict(row) for row in result.mappings()]


async def confirmed_cash_by_currency(
    db: AsyncSession, *, org_id: int, start: datetime, end: datetime,
    offer_uuid: str | None = None,
    offer_version_uuid: str | None = None,
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
    totals, _ = await _confirmed_cash_rollup(
        db, org_id=org_id, start=start, end=end, offer_uuid=offer_uuid,
        offer_version_uuid=offer_version_uuid,
    )
    return totals


async def _confirmed_cash_rollup(
    db: AsyncSession, *, org_id: int, start: datetime, end: datetime,
    offer_uuid: str | None, offer_version_uuid: str | None,
) -> tuple[list[dict[str, int | str]], list[SettledRefundReason]]:
    if (
        org_id < 1 or start.utcoffset() is None or end.utcoffset() is None or start >= end
        or (offer_version_uuid is not None and offer_uuid is None)
    ):
        raise ValueError("An organization and an ordered, timezone-aware period are required.")
    result = await db.execute(text("""
        WITH offer_orders AS (
            SELECT l.order_id
            FROM st_order_line l
            JOIN st_offer o ON o.org_id = l.org_id AND o.id = l.offer_id
            JOIN st_offer_version v ON v.offer_id = l.offer_id AND v.version_no = l.offer_version_no
            WHERE l.org_id = :org_id
            GROUP BY l.order_id
            HAVING bool_and(
                o.offer_uuid = CAST(:offer_uuid AS text)
                AND (CAST(:offer_version_uuid AS text) IS NULL OR v.version_uuid = CAST(:offer_version_uuid AS text))
            )
        ), cash AS (
            SELECT m.order_id, m.currency, m.amount_minor, 'collection' AS kind,
                   e.settled_at AS effective_at, NULL::text AS reason_code
            FROM st_money_movement m
            JOIN st_manual_collection_evidence e
              ON e.org_id = m.org_id AND e.order_id = m.order_id AND e.attempt_id = m.attempt_id
            WHERE m.org_id = :org_id
            UNION ALL
            SELECT r.order_id, r.currency, r.amount_minor, 'refund', s.settled_at, r.reason_code
            FROM st_refund_settlement s
            JOIN st_refund_reservation r ON r.org_id = s.org_id AND r.id = s.reservation_id
            WHERE s.org_id = :org_id
            UNION ALL
            SELECT order_id, currency, amount_minor, 'chargeback', effective_at, NULL::text
            FROM st_chargeback_adjustment WHERE org_id = :org_id
            UNION ALL
            SELECT a.order_id, a.currency, a.amount_minor, 'reversal', r.effective_at, NULL::text
            FROM st_chargeback_reversal r
            JOIN st_chargeback_adjustment a ON a.org_id = r.org_id AND a.id = r.adjustment_id
            WHERE r.org_id = :org_id
        )
        SELECT currency, reason_code,
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
        GROUP BY GROUPING SETS ((currency), (currency, kind, reason_code))
        HAVING GROUPING(reason_code) = 1 OR (kind = 'refund' AND reason_code IS NOT NULL)
        ORDER BY currency, reason_code NULLS FIRST
    """), {
        "org_id": org_id, "start": start, "end": end, "offer_uuid": offer_uuid,
        "offer_version_uuid": offer_version_uuid,
    })
    totals = []
    reasons = []
    for row in result.mappings():
        if row["reason_code"] is None:
            totals.append({
                key: value if key == "currency" else int(value)
                for key, value in row.items() if key != "reason_code"
            })
        else:
            reasons.append(SettledRefundReason(
                currency=row["currency"], reason_code=row["reason_code"],
                settlement_count=row["settled_refund_count"], refunded_minor=row["refunded_minor"],
            ))
    return totals, reasons
