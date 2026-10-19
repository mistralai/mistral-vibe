from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

from vibe.app_server.client import AppServerClient, AppServerConnectionClosed
from vibe.app_server.connector_catalog import ConnectorRuntimeAuthorization
from vibe.app_server.events import ConnectorAuthorizationRequiredEvent, TurnCompleted
from vibe.app_server.models import (
    PublicQueuedTurn,
    PublicTurn,
    PublicTurnQueue,
    PublicTurnStatus,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    CallbackCallResponse,
    ConnectorAuthRequiredParams,
    Notification,
    ProtocolError,
    ProtocolErrorCode,
    RuntimeUpdatedParams,
    ServerRequest,
    SessionTextContentBlock,
    TurnUserInputEntry,
    validate_callback_acknowledgement,
)
from vibe.app_server.server import AppServer
from vibe.app_server.session import AppServerSession, _PublishedEvent
from vibe.app_server.transport import memory_transport_pair


@pytest.mark.asyncio
async def test_connector_auth_notification_failure_releases_reservation() -> None:
    release_reservation = Mock()
    start_broker = Mock()
    service = SimpleNamespace(
        accept_auth_required=AsyncMock(
            return_value=ConnectorRuntimeAuthorization(
                runtime_updated=cast(RuntimeUpdatedParams, object()),
                start_broker=start_broker,
                release_reservation=release_reservation,
            )
        )
    )
    server = SimpleNamespace(
        _connector_catalog_service=service,
        _root=None,
        _notify=AsyncMock(),
        _route_notification=AsyncMock(side_effect=RuntimeError("disconnected")),
    )
    event = ConnectorAuthorizationRequiredEvent(
        params=ConnectorAuthRequiredParams(
            session_id="session-1",
            alias="github",
            accepted_catalog_revision="catalog-1",
            reason="gateway_rejected",
        ),
        raw_connector_id="github/raw",
        action="oauth",
    )

    with pytest.raises(RuntimeError, match="disconnected"):
        await AppServer._forward_connector_authorization(cast(AppServer, server), event)

    release_reservation.assert_called_once_with()
    start_broker.assert_not_called()


def test_callback_delivery_acknowledgement_has_no_semantic_output() -> None:
    response = CallbackCallResponse(callback_id="approval-1")

    assert validate_callback_acknowledgement("approval-1", response) is response
    assert response.model_dump(mode="json", by_alias=True) == {
        "callbackId": "approval-1",
        "accepted": True,
    }

    with pytest.raises(ValueError, match="does not match"):
        validate_callback_acknowledgement("approval-2", response)


@pytest.mark.asyncio
async def test_interrupt_waits_for_pending_turn_events() -> None:
    session = _interrupt_session()
    client = _InterruptClient({"accepted": True, "lastEventId": 1})
    session._ensure_attached = AsyncMock(return_value=client)

    await session.interrupt()

    assert client.requests == [
        {
            "method": "turn/interrupt",
            "session_id": "session-1",
            "expected_turn_id": "turn-1",
            "wait_for_incoming": True,
        }
    ]


@pytest.mark.asyncio
async def test_inject_user_context_require_active_turn_does_not_idle_inject() -> None:
    session = object.__new__(AppServerSession)
    session._active_public_turn_id = Mock(return_value=None)
    client = _InterruptClient({"entries": []})
    session._ensure_attached = AsyncMock(return_value=client)

    with pytest.raises(AppServerResponseError, match="No active turn") as exc_info:
        await session.inject_user_context("follow up", require_active_turn=True)

    assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
    assert client.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ProtocolError(code=ProtocolErrorCode.STALE_TURN, message="Stale turn"),
        ProtocolError(code=ProtocolErrorCode.CONFLICT, message="No active turn"),
        ProtocolError(
            code=ProtocolErrorCode.CONFLICT, message="No matching active turn"
        ),
    ],
)
async def test_interrupt_ignores_already_settled_turn_errors(
    error: ProtocolError,
) -> None:
    session = _interrupt_session()
    session._ensure_attached = AsyncMock(
        return_value=_InterruptClient(AppServerResponseError(error))
    )

    await session.interrupt()


@pytest.mark.asyncio
async def test_interrupt_waits_for_a_starting_turn_to_surface() -> None:
    session = _interrupt_session()
    session._starting_turn = True
    session._turn_started = asyncio.Event()
    session._active_public_turn_id = Mock(return_value=None)
    client = _InterruptClient({"accepted": True, "lastEventId": 1})
    session._ensure_attached = AsyncMock(return_value=client)

    async def surface_turn() -> None:
        await asyncio.sleep(0.01)
        session._active_public_turn_id = Mock(return_value="turn-1")
        session._turn_started.set()

    await asyncio.gather(session.interrupt(), surface_turn())

    assert client.requests == [
        {
            "method": "turn/interrupt",
            "session_id": "session-1",
            "expected_turn_id": "turn-1",
            "wait_for_incoming": True,
        }
    ]


@pytest.mark.asyncio
async def test_interrupt_gives_up_when_a_starting_turn_never_surfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.app_server.session._INTERRUPT_WAIT_FOR_TURN_START_SECONDS", 0.05
    )
    session = _interrupt_session()
    session._starting_turn = True
    session._turn_started = asyncio.Event()
    session._active_public_turn_id = Mock(return_value=None)
    client = _InterruptClient({"accepted": True, "lastEventId": 1})
    session._ensure_attached = AsyncMock(return_value=client)

    await session.interrupt()

    assert client.requests == []


@pytest.mark.asyncio
async def test_interrupt_during_the_act_prologue_cancels_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _act_prologue_session()
    client = _ActPrologueClient({
        "turn": {
            "id": "turn-1",
            "sessionId": "session-1",
            "status": "in_progress",
            "startedAt": 1,
        }
    })
    prologue_started = asyncio.Event()
    release_prologue = asyncio.Event()

    async def slow_ensure_attached() -> Any:
        prologue_started.set()
        await release_prologue.wait()
        return client

    monkeypatch.setattr(session, "_ensure_attached", slow_ensure_attached)
    consumer = asyncio.create_task(_consume(session.act("hello")))
    try:
        await asyncio.wait_for(prologue_started.wait(), timeout=1)
        # The prologue marks the turn as starting before the turn/start request,
        # so a cancel arriving now must wait for the turn to surface.
        assert session.turn_active
        assert not session._turn_started.is_set()
        interrupt = asyncio.create_task(session.interrupt())
        await asyncio.sleep(0.05)
        assert not interrupt.done()
        session._active_public_turn_id = Mock(return_value="turn-1")
        release_prologue.set()
        await asyncio.wait_for(interrupt, timeout=5)
        session._events.put_nowait(
            _PublishedEvent(
                generation=0,
                event=TurnCompleted(
                    turn=PublicTurn(
                        id="turn-1",
                        session_id="session-1",
                        status=PublicTurnStatus.COMPLETED,
                        started_at=1,
                    )
                ),
            )
        )
        await asyncio.wait_for(consumer, timeout=5)
    finally:
        if not consumer.done():
            consumer.cancel()
            with suppress(asyncio.CancelledError):
                await consumer

    interrupt_requests = [
        request for request in client.requests if request["method"] == "turn/interrupt"
    ]
    assert interrupt_requests == [
        {
            "method": "turn/interrupt",
            "session_id": "session-1",
            "expected_turn_id": "turn-1",
            "wait_for_incoming": True,
        }
    ]
    assert session._turn_started.is_set()
    assert not session._starting_turn


@pytest.mark.asyncio
async def test_cancelled_client_request_is_removed_from_pending_requests() -> None:
    client_transport, _ = memory_transport_pair()
    client = AppServerClient(client_transport)
    task = asyncio.create_task(client.request("test/wait"))

    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert client._pending == {}
    await client.close()


@pytest.mark.asyncio
async def test_transport_eof_fails_pending_requests() -> None:
    client_transport, server_transport = memory_transport_pair()
    client = AppServerClient(client_transport)
    task = asyncio.create_task(client.request("test/wait"))

    await asyncio.sleep(0)
    await server_transport.close()

    with pytest.raises(AppServerConnectionClosed):
        await task
    assert client._pending == {}
    await client.close()


@pytest.mark.asyncio
async def test_server_messages_preserve_wire_order() -> None:
    client_transport, server_transport = memory_transport_pair()
    client = AppServerClient(client_transport)
    await client.start()

    await server_transport.send({
        "jsonrpc": "2.0",
        "method": "history/entryAdded",
        "params": {"entry": {}},
    })
    await server_transport.send({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "callback/call",
        "params": {},
    })

    incoming = client.incoming()
    first = await anext(incoming)
    second = await anext(incoming)

    assert isinstance(first, Notification)
    assert first.method == "history/entryAdded"
    assert isinstance(second, ServerRequest)
    assert second.method == "callback/call"

    await incoming.aclose()
    await client.close()
    await server_transport.close()


@pytest.mark.asyncio
async def test_queued_turn_edit_methods_map_the_public_requests() -> None:
    """*Prepare*: A client facade with an existing queued message.
    *Do*: Start, save, cancel, and abandon an edit through its typed methods.
    *Assert*: Every request preserves queue and edit identities.
    """
    # Prepare
    queued = PublicQueuedTurn(
        id="queue-1",
        created_at=1,
        entries=[
            TurnUserInputEntry(content=[SessionTextContentBlock(text="original")])
        ],
    )
    projection = Mock()
    session = object.__new__(AppServerSession)
    session._state = cast(
        Any,
        SimpleNamespace(
            session_id="session-1",
            state=SimpleNamespace(turn_queue=PublicTurnQueue(items=[queued])),
            projection=projection,
        ),
    )
    client = _QueueEditClient()
    session._ensure_attached = AsyncMock(return_value=client)
    edit_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

    # Do
    started = await session.start_queued_turn_edit(queued.id, edit_id)
    saved = await session.save_queued_turn_edit(
        queued.id,
        edit_id,
        "edited",
        message_entry_id="user-1",
        idempotency_key="save-1",
    )
    await session.cancel_queued_turn_edit(queued.id, edit_id)
    await session.abandon_queued_turn_edit(queued.id, edit_id)

    # Assert
    assert started.queue_item_id == queued.id
    assert saved.id == queued.id
    assert [request["method"] for request in client.requests] == [
        "session/turn/queue/edit/start",
        "session/turn/queue/edit/save",
        "session/turn/queue/edit/cancel",
        "session/turn/queue/edit/abandon",
    ]
    assert [request["params"] for request in client.requests] == [
        {"sessionId": "session-1", "queueItemId": "queue-1", "editId": str(edit_id)},
        {
            "sessionId": "session-1",
            "queueItemId": "queue-1",
            "editId": str(edit_id),
            "idempotencyKey": "save-1",
            "entries": [
                {
                    "role": "user",
                    "entryId": "user-1",
                    "content": [{"type": "text", "text": "edited"}],
                    "annotations": {},
                }
            ],
        },
        {"sessionId": "session-1", "queueItemId": "queue-1", "editId": str(edit_id)},
        {"sessionId": "session-1", "queueItemId": "queue-1", "editId": str(edit_id)},
    ]
    projection.replace_queued_turn.assert_called_once_with(saved)


async def _next_event[EventT](
    events: AsyncIterator[object], event_type: type[EventT]
) -> EventT:
    async for event in events:
        if isinstance(event, event_type):
            return event
    raise AssertionError(f"Event stream ended before {event_type.__name__}")


def _interrupt_session() -> AppServerSession:
    session = object.__new__(AppServerSession)
    session._state = cast(Any, SimpleNamespace(session_id="session-1"))
    session._active_public_turn_id = Mock(return_value="turn-1")
    return cast(AppServerSession, session)


class _QueueEditClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    async def request(
        self, method: str, params: Any, *, wait_for_incoming: bool = False
    ) -> dict[str, Any]:
        self.requests.append({
            "method": method,
            "params": params.model_dump(mode="json"),
            "wait_for_incoming": wait_for_incoming,
        })
        if method in {
            "session/turn/queue/edit/cancel",
            "session/turn/queue/edit/abandon",
        }:
            return {}
        if method == "session/turn/queue/edit/start":
            return {"queueItemId": params.queue_item_id}
        return {"queueItemId": params.queue_item_id}


class _InterruptClient:
    def __init__(self, result: dict[str, Any] | Exception) -> None:
        self._result = result
        self.requests: list[dict[str, Any]] = []

    async def request(
        self, method: str, params: Any, *, wait_for_incoming: bool = False
    ) -> dict[str, Any]:
        self.requests.append({
            "method": method,
            "session_id": params.session_id,
            "expected_turn_id": params.expected_turn_id,
            "wait_for_incoming": wait_for_incoming,
        })
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


async def _consume(stream: AsyncIterator[Any]) -> list[Any]:
    return [event async for event in stream]


def _act_prologue_session() -> AppServerSession:
    session = object.__new__(AppServerSession)
    session._state = cast(
        Any,
        SimpleNamespace(
            session_id="session-1",
            state=SimpleNamespace(turns=[]),
            projection=SimpleNamespace(begin_turn=Mock()),
        ),
    )
    session._active_public_turn_id = Mock(return_value=None)
    session._starting_turn = False
    session._turn_started = asyncio.Event()
    session._turn_started.set()
    session._consumed_turn_id = None
    session._events = asyncio.Queue()
    session._event_generation = 0
    session.resources = cast(
        Any, SimpleNamespace(runtime=SimpleNamespace(refresh=AsyncMock()))
    )
    return cast(AppServerSession, session)


class _ActPrologueClient:
    """Answers turn/start with a fixed turn and records every request."""

    def __init__(self, turn_start_wire: dict[str, Any]) -> None:
        self._turn_start_wire = turn_start_wire
        self.requests: list[dict[str, Any]] = []

    async def request(
        self, method: str, params: Any, *, wait_for_incoming: bool = False
    ) -> dict[str, Any]:
        self.requests.append({
            "method": method,
            "session_id": params.session_id,
            "expected_turn_id": getattr(params, "expected_turn_id", None),
            "wait_for_incoming": wait_for_incoming,
        })
        if method == "turn/start":
            return self._turn_start_wire
        return {"accepted": True, "lastEventId": 1}
