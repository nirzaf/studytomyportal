import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError

from src.db.organizations import Organization
from src.db.studytomy.capacity import StudyCapacityHold
from src.db.studytomy.commerce import StudyOffer
from src.db.users import User
from src.services.studytomy.capacity import (
    consume_offer_capacity,
    reserve_offer_capacity,
)


@pytest.mark.studytomy_postgres
async def test_last_place_and_late_confirmation(postgres_session_factory):
    token = uuid4().hex
    org = Organization(
        name="Synthetic capacity", slug=f"capacity-{token}", org_uuid=f"org_{token}",
        email=f"capacity-{token}@example.invalid",
    )
    user = User(
        username=f"staff-{token}", user_uuid=f"user_{token}",
        email=f"staff-{token}@example.invalid", first_name="Synthetic", last_name="Staff",
    )
    now = datetime.now(UTC)
    async with postgres_session_factory() as seed:
        seed.add_all([org, user])
        await seed.flush()
        offer = StudyOffer(
            org_id=org.id, offer_uuid=token, offer_kind="course", created_by_user_id=user.id,
        )
        seed.add(offer)
        await seed.flush()
        seed.add(StudyCapacityHold(
            offer_id=offer.id, request_uuid="expired", places=1,
            created_at=now - timedelta(minutes=2), expires_at=now - timedelta(minutes=1),
        ))
        await seed.commit()

    scope = {"org_id": org.id, "offer_id": offer.id}
    terms = {"places": 1, "approved_limit": 1, "approved_hold_duration": timedelta(minutes=5)}
    async with postgres_session_factory() as first, postgres_session_factory() as second:
        hold = await reserve_offer_capacity(
            first, **scope, **terms, request_uuid="first", sales_open=True,
        )
        rival = asyncio.create_task(reserve_offer_capacity(
            second, **scope, **terms, request_uuid="second", sales_open=True,
        ))
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(rival), timeout=0.2)
            await first.commit()
            with pytest.raises(HTTPException) as unavailable:
                await asyncio.wait_for(rival, timeout=5)
            assert unavailable.value.detail["code"] == "OFFER_CAPACITY_UNAVAILABLE"
        finally:
            if not rival.done():
                rival.cancel()
                await asyncio.gather(rival, return_exceptions=True)
        await second.rollback()

        for request_uuid, sales_open in (("expired", True), ("first", False)):
            with pytest.raises(HTTPException) as review:
                await consume_offer_capacity(first, **scope, request_uuid=request_uuid, sales_open=sales_open)
            assert review.value.detail["code"] == "CAPACITY_REMEDY_REVIEW_REQUIRED"
        consumed = await consume_offer_capacity(first, **scope, request_uuid="first", sales_open=True)
        assert consumed.id == hold.id
        consumed_at = consumed.consumed_at
        await first.commit()
        replay = await consume_offer_capacity(second, **scope, request_uuid="first", sales_open=False)
        assert replay.consumed_at == consumed_at
        for statement, message in (
            (update(StudyCapacityHold).values(places=2), "history is immutable"),
            (update(StudyCapacityHold).values(consumed_at=None), "history is immutable"),
            (delete(StudyCapacityHold), "history cannot be deleted"),
        ):
            with pytest.raises(DBAPIError, match=message):
                async with second.begin_nested():
                    await second.execute(statement.where(StudyCapacityHold.id == hold.id))
