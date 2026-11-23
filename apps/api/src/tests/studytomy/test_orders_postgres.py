import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.db.organizations import Organization
from src.db.studytomy.commerce import StudyOffer, StudyOfferVersion
from src.db.studytomy.family import StudyLearnerProfile
from src.db.studytomy.governance import StudyBusinessEvent
from src.db.studytomy.orders import StudyOrder, StudyOrderAcceptance, StudyOrderLine
from src.db.users import User
from src.services.studytomy import orders


@pytest.mark.studytomy_postgres
async def test_order_lines_bind_named_learners_and_exact_offer_version(
    postgres_session_factory, monkeypatch,
):
    token = uuid4().hex
    org = Organization(
        name="Synthetic order", slug=token, org_uuid=f"org_{token}", email=f"{token}@example.invalid",
    )
    users = [
        User(
            username=f"{token}-{i}", user_uuid=f"user_{token}_{i}",
            email=f"{token}-{i}@example.invalid", first_name="Synthetic", last_name=f"Account {i}",
        )
        for i in range(3)
    ]
    async with postgres_session_factory() as db:
        db.add_all([org, *users])
        await db.flush()
        purchaser, first, second = users
        learners = [
            StudyLearnerProfile(org_id=org.id, user_id=user.id, created_by_user_id=purchaser.id)
            for user in (first, second)
        ]
        offer = StudyOffer(
            org_id=org.id, offer_uuid=token, offer_kind="course", created_by_user_id=purchaser.id,
        )
        order = StudyOrder(org_id=org.id, purchaser_user_id=purchaser.id, currency="AUD", total_minor=200)
        db.add_all([*learners, offer, order])
        await db.flush()
        current_version = StudyOfferVersion(
            offer_id=offer.id, version_no=1, terms={}, content_sha256="a" * 64,
            created_by_user_id=purchaser.id,
        )
        db.add(current_version)
        await db.flush()

        def line(learner, version):
            return StudyOrderLine(
                org_id=org.id, order_id=order.id, offer_id=offer.id, offer_version_no=version,
                learner_profile_id=learner.id, total_minor=100, terms_snapshot={},
            )

        db.add_all([line(learner, 1) for learner in learners])
        await db.flush()
        for invalid, constraint in (
            (line(learners[0], 1), "uq_st_order_line_allocation"),
            (line(learners[0], 2), "fk_st_order_line_version"),
        ):
            if invalid.offer_version_no == 2:
                # Use a separate order so the duplicate constraint cannot mask the version check.
                other = StudyOrder(
                    org_id=org.id, purchaser_user_id=purchaser.id, currency="AUD", total_minor=100,
                )
                db.add(other)
                await db.flush()
                invalid.order_id = other.id
            with pytest.raises(IntegrityError, match=constraint):
                async with db.begin_nested():
                    db.add(invalid)
                    await db.flush()
        await db.commit()

    args = {
        "purchaser_id": purchaser.id, "offer_uuid": offer.offer_uuid,
        "offer_version_uuid": current_version.version_uuid,
        "learner_uuids": [learner.learner_uuid for learner in learners],
        "confirmation_sha256": "b" * 64, "acknowledged_overlap": False,
    }
    verifier = AsyncMock(return_value=orders.NamedOrderQuote(
        offer_version_uuid=current_version.version_uuid, offer_sha256=current_version.content_sha256,
        price_per_learner={"currency": "AUD", "total_minor": 100,
                           "lines": [{"kind": "charge", "label": "Synthetic", "amount_minor": 100}]},
        learner_count=2, total_minor=200, learner_uuids=args["learner_uuids"],
        learners_with_overlap=[], customer_terms="Synthetic", confirmation_sha256="b" * 64,
    ))
    monkeypatch.setattr(orders, "verify_named_order_confirmation", verifier)
    async with postgres_session_factory() as first, postgres_session_factory() as second:
        reference = await orders.record_named_order_draft(first, org, **args, idempotency_key="parallel-order")
        rival = asyncio.create_task(orders.record_named_order_draft(
            second, org, **args, idempotency_key="parallel-order",
        ))
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(rival), timeout=0.2)
            await first.commit()
            assert await asyncio.wait_for(rival, timeout=5) == reference
        finally:
            if not rival.done():
                rival.cancel()
                await asyncio.gather(rival, return_exceptions=True)
        verifier.assert_awaited_once()
        saved = (await second.execute(select(StudyOrder).where(
            StudyOrder.customer_ref == reference,
        ))).scalar_one()
        assert saved.sealed_at is not None
        assert saved.confirmation_sha256 == args["confirmation_sha256"]
        lines = (await second.execute(select(StudyOrderLine).where(StudyOrderLine.order_id == saved.id))).scalars().all()
        assert {line.learner_profile_id for line in lines} == {learner.id for learner in learners}
        event = (await second.execute(select(StudyBusinessEvent).where(
            StudyBusinessEvent.org_id == org.id, StudyBusinessEvent.resource_uuid == reference,
        ))).scalar_one()
        assert event.action == "studytomy.order.draft_recorded"
        for statement in (
            update(StudyOrder).where(StudyOrder.id == saved.id).values(currency="USD"),
            update(StudyOrderLine).where(StudyOrderLine.id == lines[0].id).values(total_minor=1),
            delete(StudyOrderLine).where(StudyOrderLine.id == lines[0].id),
        ):
            with pytest.raises(DBAPIError, match="Order quote and allocation history is immutable"):
                async with second.begin_nested():
                    await second.execute(statement)
        with pytest.raises(DBAPIError, match="Sealed order allocations cannot be extended"):
            async with second.begin_nested():
                await second.execute(insert(StudyOrderLine).values(
                    org_id=org.id, order_id=saved.id, offer_id=offer.id, offer_version_no=1,
                    learner_profile_id=learners[0].id, total_minor=100, terms_snapshot={},
                    created_at=saved.sealed_at,
                ))
        acceptance = insert(StudyOrderAcceptance).values(
            org_id=org.id, order_id=saved.id, confirmation_sha256="b" * 64,
            acknowledged_overlap=False, accepted_at=saved.sealed_at,
            policy_binding={"fixture": "synthetic"},
        )
        with pytest.raises(DBAPIError, match="requires the sealed purchaser draft"):
            async with second.begin_nested():
                await second.execute(acceptance.values(purchaser_user_id=learners[0].user_id))
        with pytest.raises(DBAPIError, match="requires the sealed purchaser draft"):
            async with second.begin_nested():
                await second.execute(acceptance.values(
                    purchaser_user_id=purchaser.id, confirmation_sha256="c" * 64,
                ))
        await second.execute(acceptance.values(purchaser_user_id=purchaser.id))
        with pytest.raises(IntegrityError):
            async with second.begin_nested():
                await second.execute(acceptance.values(purchaser_user_id=purchaser.id))
        with pytest.raises(DBAPIError, match="Order quote and allocation history is immutable"):
            async with second.begin_nested():
                await second.execute(delete(StudyOrderAcceptance).where(
                    StudyOrderAcceptance.order_id == saved.id,
                ))
        await second.commit()
