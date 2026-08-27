from __future__ import annotations

import asyncio
from typing import cast

import pytest

from tests.conftest import build_test_agent_loop
from tests.stubs.app_server import build_test_app_server
from vibe.app_server._legacy_session_backend import (
    LegacySessionBackend,
    LegacySessionBackendHost,
)
from vibe.app_server._session_backend_port import (
    SessionBackend,
    SessionBackendError,
    SessionBackendHost,
)
from vibe.app_server.client import AppServerClient
from vibe.app_server.events import HistoryEntryAdded, ServerWarning
from vibe.app_server.models import PublicError, TextContentBlock
from vibe.app_server.protocol import (
    ClientCapabilities,
    ClientInfo,
    ContextInjectParams,
    ProtocolErrorCode,
    ServerWarningParams,
    SessionReadParams,
    SessionSettingsUpdateParams,
)
from vibe.app_server.server import _SESSION_BACKEND_METHODS, AppServer
from vibe.app_server.session import AppServerSession
from vibe.app_server.transport import memory_transport_pair


def test_session_backend_contract_covers_the_complete_session_lifecycle() -> None:
    assert _protocol_members(SessionBackend) == {
        "compact",
        "guard_request",
        "inject_context",
        "interrupt_turn",
        "read",
        "reload_config",
        "respond_to_callback",
        "session_id",
        "shutdown",
        "start_turn",
        "steer_turn",
        "subscribe",
        "switch_agent",
        "update_settings",
        "write_config",
    }

    assert _SESSION_BACKEND_METHODS == {
        "callback/result",
        "config/reload",
        "config/write",
        "session/agent/update",
        "session/compact",
        "session/context/inject",
        "session/settings/update",
        "turn/interrupt",
        "turn/start",
        "turn/steer",
    }


def test_session_backend_host_contract_owns_session_selection() -> None:
    assert _protocol_members(SessionBackendHost) == {
        "continue_latest",
        "fork",
        "harness_kind",
        "list",
        "read",
        "resume",
        "shutdown",
        "start",
    }


def test_session_backend_errors_keep_semantic_code_and_data() -> None:
    error = SessionBackendError(
        ProtocolErrorCode.STALE_TURN,
        "The active turn changed",
        {"activeTurnId": "turn-2"},
    )

    assert error.code is ProtocolErrorCode.STALE_TURN
    assert error.data == {"activeTurnId": "turn-2"}
    assert str(error) == "The active turn changed"
    assert ProtocolErrorCode.CALLBACK_CLOSED.value == "callback_closed"


def test_app_server_passes_services_to_session_backend_host_factory() -> None:
    captured_services: object | None = None
    expected_host = cast(SessionBackendHost, object())

    def factory(services: object) -> SessionBackendHost:
        nonlocal captured_services
        captured_services = services
        return expected_host

    _, server_transport = memory_transport_pair()
    server = AppServer(server_transport, session_backend_host_factory=factory)

    assert captured_services is server
    assert server._session_backend_host is expected_host


def test_app_server_rejects_empty_session_backend_host_factory_result() -> None:
    def empty_factory(_: object) -> SessionBackendHost:
        return cast(SessionBackendHost, None)

    _, server_transport = memory_transport_pair()

    with pytest.raises(TypeError, match="must return a SessionBackendHost"):
        AppServer(server_transport, session_backend_host_factory=empty_factory)


@pytest.mark.asyncio
async def test_app_server_root_is_the_legacy_session_backend() -> None:
    client_transport, server_transport = memory_transport_pair()
    server = build_test_app_server(build_test_agent_loop(), server_transport)
    client = AppServerClient(client_transport, run_peer=server.serve)
    session = await AppServerSession.start(
        client,
        client_info=ClientInfo(name="test", version="0"),
        capabilities=ClientCapabilities(),
    )
    try:
        host = server._session_backend_host
        backend = server._require_root()
        _accept_session_backend_host(host)
        _accept_session_backend(backend)
        assert isinstance(backend, LegacySessionBackend)

        event_task = server._backend_event_task
        assert event_task is not None
        event_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await event_task

        response = await backend.read(SessionReadParams(session_id=session.session_id))
        subscription = await backend.subscribe(
            SessionReadParams(session_id=session.session_id)
        )
        with pytest.raises(SessionBackendError) as exc_info:
            await backend.subscribe(SessionReadParams(session_id=session.session_id))
        assert exc_info.value.code is ProtocolErrorCode.CONFLICT
        injected = await backend.inject_context(
            ContextInjectParams(
                session_id=session.session_id,
                input=[TextContentBlock(text="remember this")],
                as_message=True,
            )
        )
        envelope = await anext(subscription.events)
        await backend.inject_context(
            ContextInjectParams(
                session_id=session.session_id,
                input=[TextContentBlock(text="drop this event")],
                as_message=True,
            )
        )
        backend._events.get_nowait()
        await backend.inject_context(
            ContextInjectParams(
                session_id=session.session_id,
                input=[TextContentBlock(text="detect the gap")],
                as_message=True,
            )
        )
        settings_response = await backend.update_settings(
            SessionSettingsUpdateParams(session_id=session.session_id, max_turns=3)
        )

        assert isinstance(host, LegacySessionBackendHost)
        assert response.state.session.id == session.session_id
        assert subscription.snapshot.state.session.id == session.session_id
        assert (
            subscription.snapshot.last_event_id == subscription.snapshot.state.event_id
        )
        assert isinstance(envelope.event, HistoryEntryAdded)
        assert envelope.event.entry == injected.response.entries[0]
        assert settings_response.response.model_dump() == {}
        with pytest.raises(SessionBackendError) as gap_exc_info:
            await anext(subscription.events)
        assert gap_exc_info.value.code is ProtocolErrorCode.STALE_CURSOR
        replacement = await backend.subscribe(
            SessionReadParams(session_id=session.session_id)
        )
        assert replacement.snapshot.state.session.id == session.session_id
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_legacy_backend_subscription_forwards_direct_events_and_closes() -> None:
    client_transport, server_transport = memory_transport_pair()
    server = build_test_app_server(build_test_agent_loop(), server_transport)
    client = AppServerClient(client_transport, run_peer=server.serve)
    session = await AppServerSession.start(
        client,
        client_info=ClientInfo(name="test", version="0"),
        capabilities=ClientCapabilities(),
    )
    backend = server._require_root()
    assert isinstance(backend, LegacySessionBackend)
    event_task = server._backend_event_task
    assert event_task is not None
    event_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await event_task
    subscription = await backend.subscribe(
        SessionReadParams(session_id=session.session_id)
    )

    handled = await backend.publish_notification(
        "warning", ServerWarningParams(warning=PublicError(message="careful"))
    )
    envelope = await anext(subscription.events)
    ignored = await backend.publish_notification(
        "unknown/event", ServerWarningParams(warning=PublicError(message="ignored"))
    )

    assert handled is True
    assert ignored is False
    assert isinstance(envelope.event, ServerWarning)
    assert envelope.event_id is None
    assert envelope.method == "warning"

    next_event = asyncio.ensure_future(anext(subscription.events))
    await backend.shutdown()
    with pytest.raises(StopAsyncIteration):
        await next_event
    await client.close()


def _accept_session_backend(backend: SessionBackend) -> None:
    pass


def _accept_session_backend_host(backend: SessionBackendHost) -> None:
    pass


def _protocol_members(protocol: type[object]) -> set[str]:
    return {name for name in protocol.__dict__ if not name.startswith("_")}
