"""Request-local settlement for reserved playground generation credits."""

import logging
from contextlib import contextmanager
from uuid import uuid4

from anyio import CancelScope
from fastapi.responses import StreamingResponse

from src.security.features_utils.usage import refund_ai_credit


logger = logging.getLogger(__name__)


class PlaygroundCreditStream(StreamingResponse):
    """Own the reservation through ASGI send, including an unstarted body."""

    def __init__(self, reservation, body, **kwargs):
        super().__init__(body, **kwargs)
        self.reservation = reservation

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.reservation.close()
            with CancelScope(shield=True):
                try:
                    await self.body_iterator.aclose()
                except Exception:
                    logger.warning("Could not close playground stream")


class PlaygroundCreditReservation:
    """Create only after reserve_ai_credit returns successfully."""

    def __init__(self, org_id: int):
        self.org_id = org_id
        self.refund_id = uuid4().hex
        self.unsettled = True
        self.transferred = False

    def complete(self):
        self.unsettled = False

    def close(self):
        if not self.unsettled:
            return
        self.unsettled = False
        try:
            refund_ai_credit(
                self.org_id,
                amount=3,
                idempotency_key=self.refund_id,
            )
        except Exception:
            # The same key makes one retry safe if Redis applied the script but
            # the response was lost. This is not durable crash reconciliation.
            try:
                refund_ai_credit(
                    self.org_id,
                    amount=3,
                    idempotency_key=self.refund_id,
                )
            except Exception:
                logger.warning(
                    "Could not confirm playground credit refund for org %s",
                    self.org_id,
                )

    @contextmanager
    def setup(self):
        try:
            yield
        finally:
            if not self.transferred:
                self.close()

    def response(self, body, **kwargs):
        response = PlaygroundCreditStream(self, body, **kwargs)
        self.transferred = True
        return response
