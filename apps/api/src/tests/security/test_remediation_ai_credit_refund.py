"""
F-11: AI credits are refunded if the streaming generator aborts before
producing any model output.

These unit-test the SSE generator directly; the router wiring that injects
``org_id`` is covered by the existing F-9 rate-limit tests.
"""

import asyncio
from unittest.mock import patch

import pytest


async def _collect(gen):
    chunks = []
    async for c in gen:
        chunks.append(c)
    return chunks


async def _empty_stream():
    """A stream that yields nothing (e.g. upstream model refused)."""
    if False:
        yield ""  # pragma: no cover (never runs, makes this an async gen)


async def _raising_stream():
    """Stream that raises before producing any chunk."""
    raise RuntimeError("upstream provider 500")
    yield  # pragma: no cover


async def _cancelled_stream():
    """Stream that gets cancelled (client disconnect) before yielding."""
    raise asyncio.CancelledError()
    yield  # pragma: no cover


@pytest.mark.asyncio
async def test_activity_chat_refunds_credit_on_upstream_exception():
    """F-11: model error → credit refund, org quota preserved."""
    from src.routers.ai.ai import activity_chat_event_generator

    refunded = []
    with patch(
        "src.security.features_utils.usage.refund_ai_credit",
        side_effect=lambda org_id, amount=1: refunded.append((org_id, amount)),
    ), patch(
        "src.routers.ai.ai.save_message_to_history",
    ):
        chunks = await _collect(
            activity_chat_event_generator(
                _raising_stream(),
                "chat_uuid",
                "activity_uuid",
                "hello",
                "ai text",
                "gemini",
                org_id=77,
            )
        )

    # Should have emitted start + error events.
    assert any("error" in c for c in chunks), chunks
    assert refunded == [(77, 1)]


@pytest.mark.asyncio
async def test_activity_chat_refunds_credit_on_empty_stream():
    """F-11: stream produces nothing → credit refunded."""
    from src.routers.ai.ai import activity_chat_event_generator

    refunded = []

    async def _fake_follow_ups(*args, **kwargs):
        return []

    with patch(
        "src.security.features_utils.usage.refund_ai_credit",
        side_effect=lambda org_id, amount=1: refunded.append((org_id, amount)),
    ), patch(
        "src.routers.ai.ai.save_message_to_history",
    ), patch(
        "src.routers.ai.ai.generate_follow_up_suggestions",
        side_effect=_fake_follow_ups,
    ):
        await _collect(
            activity_chat_event_generator(
                _empty_stream(),
                "chat_uuid",
                "activity_uuid",
                "hello",
                "ai text",
                "gemini",
                org_id=77,
            )
        )

    # Empty stream → stream_failed is False but full_response is "" → refund fires.
    assert refunded == [(77, 1)]


@pytest.mark.asyncio
async def test_activity_chat_does_not_refund_on_successful_stream():
    """Sanity: real model output means no refund."""
    from src.routers.ai.ai import activity_chat_event_generator

    async def _good_stream():
        yield "hello"
        yield " world"

    refunded = []

    async def _fake_follow_ups(*args, **kwargs):
        return []

    with patch(
        "src.security.features_utils.usage.refund_ai_credit",
        side_effect=lambda org_id, amount=1: refunded.append((org_id, amount)),
    ), patch(
        "src.routers.ai.ai.save_message_to_history",
    ), patch(
        "src.routers.ai.ai.generate_follow_up_suggestions",
        side_effect=_fake_follow_ups,
    ):
        await _collect(
            activity_chat_event_generator(
                _good_stream(),
                "chat_uuid",
                "activity_uuid",
                "hello",
                "ai text",
                "gemini",
                org_id=77,
            )
        )

    # Stream succeeded with content → no refund.
    assert refunded == []


@pytest.mark.asyncio
async def test_activity_chat_refunds_on_client_disconnect():
    """F-11: CancelledError (client disconnect) → refund + re-raise."""
    from src.routers.ai.ai import activity_chat_event_generator

    refunded = []
    with patch(
        "src.security.features_utils.usage.refund_ai_credit",
        side_effect=lambda org_id, amount=1: refunded.append((org_id, amount)),
    ), patch(
        "src.routers.ai.ai.save_message_to_history",
    ):
        gen = activity_chat_event_generator(
            _cancelled_stream(),
            "chat_uuid",
            "activity_uuid",
            "hello",
            "ai text",
            "gemini",
            org_id=77,
        )

        with pytest.raises(asyncio.CancelledError):
            async for _ in gen:
                pass

    assert refunded == [(77, 1)]


@pytest.mark.asyncio
async def test_activity_chat_does_not_refund_on_disconnect_after_content():
    """Abuse guard: a client that disconnects AFTER receiving model output must
    NOT get its credit refunded, otherwise scripted disconnects yield free AI."""
    from src.routers.ai.ai import activity_chat_event_generator

    async def _content_then_cancel():
        yield "hello"
        raise asyncio.CancelledError()

    refunded = []
    with patch(
        "src.security.features_utils.usage.refund_ai_credit",
        side_effect=lambda org_id, amount=1: refunded.append((org_id, amount)),
    ), patch(
        "src.routers.ai.ai.save_message_to_history",
    ):
        gen = activity_chat_event_generator(
            _content_then_cancel(),
            "chat_uuid",
            "activity_uuid",
            "hello",
            "ai text",
            "gemini",
            org_id=77,
        )

        with pytest.raises(asyncio.CancelledError):
            async for _ in gen:
                pass

    # Content was produced before the disconnect → credit legitimately spent.
    assert refunded == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["success", "provider", "invalid", "disconnect", "before_body"])
@pytest.mark.parametrize("transport", ["board", "playground"])
async def test_playground_credit_settlement(mode, transport):
    from src.routers.boards.boards_playground import event_generator as board_events
    from src.routers.playgrounds.credit_stream import PlaygroundCreditReservation
    from src.routers.playgrounds.playgrounds_generator import event_generator as playground_events
    from src.services.playgrounds.adaptive_widget_contract import AdaptiveWidgetValidationError
    from starlette.requests import ClientDisconnect

    sent = []

    async def source():
        yield "first chunk"
        if mode == "provider":
            raise RuntimeError("provider failed")
        if mode == "invalid":
            raise AdaptiveWidgetValidationError("invalid widget")

    async def send(message):
        if mode == "before_body" or (mode == "disconnect" and message.get("body")):
            raise OSError("disconnected")
        sent.append(message)

    async def receive():
        pytest.fail("ASGI 2.4 uses send-side disconnect detection")

    reservation = PlaygroundCreditReservation(42)
    events = board_events if transport == "board" else playground_events
    with patch("src.routers.playgrounds.credit_stream.refund_ai_credit") as refund:
        with reservation.setup():
            response = reservation.response(events(source(), "session", reservation))
        scope = {"type": "http", "asgi": {"spec_version": "2.4"}}
        if mode in {"before_body", "disconnect"}:
            with pytest.raises(ClientDisconnect):
                await response(scope, receive, send)
        else:
            await response(scope, receive, send)
        reservation.close()
        body = b"".join(message.get("body", b"") for message in sent)
        if mode == "success":
            assert b'"type": "done"' in body
            refund.assert_not_called()
        else:
            assert b'"type": "done"' not in body
            if mode in {"provider", "invalid"}:
                assert b'"type": "error"' in body
            refund.assert_called_once()
            assert refund.call_args.kwargs["idempotency_key"] == reservation.refund_id
            assert refund.call_args.args == (42,)
            assert refund.call_args.kwargs["amount"] == 3


@pytest.mark.asyncio
async def test_playground_setup_failure_refunds_once_and_preserves_error():
    from src.routers.playgrounds.credit_stream import PlaygroundCreditReservation

    reservation = PlaygroundCreditReservation(42)
    with patch("src.routers.playgrounds.credit_stream.refund_ai_credit") as refund:
        with pytest.raises(ValueError, match="setup failed"), reservation.setup():
            raise ValueError("setup failed")
        reservation.close()
        refund.assert_called_once()
        assert refund.call_args.kwargs["idempotency_key"] == reservation.refund_id
        assert refund.call_args.args == (42,)
        assert refund.call_args.kwargs["amount"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["board", "playground"])
async def test_playground_stream_cancellation_refunds_once_and_propagates(transport):
    import asyncio

    from src.routers.boards.boards_playground import event_generator as board_events
    from src.routers.playgrounds.credit_stream import PlaygroundCreditReservation
    from src.routers.playgrounds.playgrounds_generator import event_generator as playground_events

    async def source():
        yield "validated-pending-content"
        await asyncio.Event().wait()

    async def send(_message):
        raise asyncio.CancelledError

    async def receive():
        pytest.fail("ASGI 2.4 uses send-side cancellation")

    reservation = PlaygroundCreditReservation(42)
    events = board_events if transport == "board" else playground_events
    with patch("src.routers.playgrounds.credit_stream.refund_ai_credit") as refund:
        with reservation.setup():
            response = reservation.response(events(source(), "session", reservation))

        with pytest.raises(asyncio.CancelledError):
            await response(
                {"type": "http", "asgi": {"spec_version": "2.4"}},
                receive,
                send,
            )

        reservation.close()
        refund.assert_called_once()
        assert refund.call_args.kwargs["idempotency_key"] == reservation.refund_id
        assert refund.call_args.args == (42,)
        assert refund.call_args.kwargs["amount"] == 3


def test_playground_refund_retries_once_with_same_idempotency_key():
    from src.routers.playgrounds.credit_stream import PlaygroundCreditReservation

    reservation = PlaygroundCreditReservation(42)
    calls = []

    def ambiguous_then_applied(org_id, *, amount, idempotency_key):
        calls.append((org_id, amount, idempotency_key))
        if len(calls) == 1:
            raise TimeoutError("ambiguous Redis response")
        return 0

    with patch(
        "src.routers.playgrounds.credit_stream.refund_ai_credit",
        side_effect=ambiguous_then_applied,
    ):
        reservation.close()
        reservation.close()

    assert calls == [(42, 3, reservation.refund_id)] * 2
