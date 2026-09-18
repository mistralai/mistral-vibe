from __future__ import annotations

import asyncio
from typing import cast

import pytest

from tests.conftest import build_test_agent_loop
from tests.stubs.app_server import build_test_app_server
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._legacy_session_backend import (
    LegacySessionBackend,
    LegacySessionBackendHost,
)
from vibe.app_server._session_backend_port import (
    SessionBackend,
    SessionBackendError,
    SessionBackendHost,
    SessionBackendQueuedTurnSteering,
    SessionBackendResult,
)
from vibe.app_server.client import AppServerClient
from vibe.app_server.events import HistoryEntryAdded, ServerWarning
from vibe.app_server.models import PublicError, TextContentBlock
from vibe.app_server.protocol import (
    ClientCapabilities,
    ClientInfo,
    ConfigWriteParams,
    ConfigWriteResponse,
    ContextInjectParams,
    ModelConfigWriteParams,
    ProtocolErrorCode,
    RuntimeMutationStatus,
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
        "enqueue_turn",
        "guard_request",
        "inject_context",
        "interrupt_turn",
        "read",
        "read_turn_queue",
        "reload_config",
        "remove_queued_turn",
        "replace_queued_turn",
        "respond_to_callback",
        "resume_turn_queue",
        "session_id",
        "shutdown",
        "start_turn",
        "steer_turn",
        "subscribe",
        "switch_agent",
        "update_settings",
        "write_config",
        "write_model_config",
    }

    assert _SESSION_BACKEND_METHODS == {
        "callback/result",
        "config/reload",
        "config/model/write",
        "config/write",
        "session/agent/update",
        "session/compact",
        "session/context/inject",
        "session/settings/update",
        "session/turn/enqueue",
        "session/turn/queue/read",
        "session/turn/queue/remove",
        "session/turn/queue/replace",
        "session/turn/queue/steer",
        "session/turn/queue/resume",
        "turn/interrupt",
        "turn/start",
        "turn/steer",
    }
    assert _protocol_members(SessionBackendQueuedTurnSteering) == {"steer_queued_turn"}


def test_session_backend_host_contract_owns_session_selection() -> None:
    assert _protocol_members(SessionBackendHost) == {
        "continue_latest",
        "fork",
        "harness_kind",
        "list",
        "read",
        "rename",
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
async def test_pin_requires_the_optional_host_capability() -> None:
    _, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport,
        session_backend_host_factory=lambda _: cast(SessionBackendHost, object()),
    )

    with pytest.raises(RequestFailure) as exc_info:
        await server._dispatch_backend_host_operation(
            "session/pin", {"sessionId": "saved-session", "pinned": True}
        )

    assert exc_info.value.code is ProtocolErrorCode.METHOD_NOT_FOUND


@pytest.mark.asyncio
async def test_app_server_shutdown_waits_for_host_cleanup() -> None:
    """*Prepare*: A Session Host whose shutdown remains active.
    *Do*: Close the App Server before allowing Host cleanup to finish.
    *Assert*: App Server shutdown waits until Host cleanup is complete.
    """
    # Prepare
    entered = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    class SlowShutdownHost:
        async def shutdown(self) -> None:
            entered.set()
            await release.wait()
            finished.set()

    _, server_transport = memory_transport_pair()
    host = SlowShutdownHost()
    server = AppServer(
        server_transport,
        session_backend_host_factory=lambda _services: cast(SessionBackendHost, host),
    )

    # Do
    closing = asyncio.create_task(server._close_root())
    await entered.wait()
    await asyncio.sleep(0)

    # Assert
    assert not closing.done()
    assert not finished.is_set()
    release.set()
    await asyncio.wait_for(closing, timeout=1)
    assert finished.is_set()


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
        assert not isinstance(backend, SessionBackendQueuedTurnSteering)

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


@pytest.mark.asyncio
async def test_a_parked_config_write_does_not_announce_the_runtime() -> None:
    """*Prepare*: A backend that parks what it is written, as a mid-turn pick is.
    *Do*: Dispatch both config writes.
    *Assert*: Neither announces `runtime/updated`. The snapshot on the response
    is what the session *will* run, which is what the caller asked for; telling
    every subscriber would say the running turn had already moved to it.
    """
    client_transport, server_transport = memory_transport_pair()
    server = build_test_app_server(build_test_agent_loop(), server_transport)
    client = AppServerClient(client_transport, run_peer=server.serve)
    session = await AppServerSession.start(
        client,
        client_info=ClientInfo(name="test", version="0"),
        capabilities=ClientCapabilities(),
    )
    try:
        root = server._require_root()
        assert isinstance(root, LegacySessionBackend)
        parked = SessionBackendResult(
            response=ConfigWriteResponse(
                runtime=root.runtime_updated_params().runtime,
                status=RuntimeMutationStatus.PENDING,
            )
        )

        class _ParkingBackend:
            async def write_config(
                self, params: ConfigWriteParams
            ) -> SessionBackendResult[ConfigWriteResponse]:
                return parked

            async def write_model_config(
                self, params: ModelConfigWriteParams
            ) -> SessionBackendResult[ConfigWriteResponse]:
                return parked

        backend = cast(SessionBackend, _ParkingBackend())
        written = await server._dispatch_backend_config(
            backend,
            "config/write",
            ConfigWriteParams(session_id=session.session_id, ops=[]).model_dump(
                mode="json", by_alias=True
            ),
        )
        picked = await server._dispatch_backend_config(
            backend,
            "config/model/write",
            ModelConfigWriteParams(
                session_id=session.session_id, model_alias="other"
            ).model_dump(mode="json", by_alias=True),
        )

        assert written is not None and written.runtime_updated is False
        assert picked is not None and picked.runtime_updated is False
    finally:
        await session.close()


def _accept_session_backend(backend: SessionBackend) -> None:
    pass


def _accept_session_backend_host(backend: SessionBackendHost) -> None:
    pass


def _protocol_members(protocol: type[object]) -> set[str]:
    return {name for name in protocol.__dict__ if not name.startswith("_")}
