from __future__ import annotations

import asyncio
from typing import cast

import pytest

from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._session_backend_port import (
    SessionBackend,
    SessionBackendError,
    SessionBackendHost,
    SessionBackendQueuedTurnEditing,
    SessionBackendQueuedTurnSteering,
)
from vibe.app_server.protocol import (
    EmptyResponse,
    ProtocolErrorCode,
    SessionMarkAsSeenParams,
)
from vibe.app_server.server import _SESSION_BACKEND_METHODS, AppServer
from vibe.app_server.transport import memory_transport_pair


def test_session_backend_contract_covers_the_complete_session_lifecycle() -> None:
    assert _protocol_members(SessionBackend) == {
        "compact",
        "enqueue_turn",
        "guard_request",
        "inject_context",
        "install_agent",
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
        "uninstall_agent",
        "update_settings",
        "write_config",
        "write_model_config",
    }

    assert _SESSION_BACKEND_METHODS == {
        "agents/install",
        "agents/uninstall",
        "callback/result",
        "config/reload",
        "config/model/write",
        "config/write",
        "session/agent/update",
        "session/compact",
        "session/context/inject",
        "session/settings/update",
        "session/turn/enqueue",
        "session/turn/queue/edit/abandon",
        "session/turn/queue/edit/cancel",
        "session/turn/queue/edit/save",
        "session/turn/queue/edit/start",
        "session/turn/queue/read",
        "session/turn/queue/remove",
        "session/turn/queue/replace",
        "session/turn/queue/steer",
        "session/turn/queue/resume",
        "turn/interrupt",
        "turn/start",
        "turn/steer",
    }
    assert _protocol_members(SessionBackendQueuedTurnEditing) == {
        "abandon_active_queued_turn_edit",
        "abandon_queued_turn_edit",
        "cancel_queued_turn_edit",
        "save_queued_turn_edit",
        "start_queued_turn_edit",
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
async def test_mark_as_seen_requires_the_optional_host_capability() -> None:
    _, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport,
        session_backend_host_factory=lambda _: cast(SessionBackendHost, object()),
    )

    with pytest.raises(RequestFailure) as exc_info:
        await server._dispatch_backend_host_operation(
            "session/markAsSeen", {"sessionId": "saved-session"}
        )

    assert exc_info.value.code is ProtocolErrorCode.METHOD_NOT_FOUND


@pytest.mark.asyncio
async def test_mark_as_seen_routes_to_the_selected_backend_host() -> None:
    received: SessionMarkAsSeenParams | None = None

    class SeenStateHost:
        async def mark_as_seen(self, params: SessionMarkAsSeenParams) -> EmptyResponse:
            nonlocal received
            received = params
            return EmptyResponse()

    _, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport,
        session_backend_host_factory=lambda _: cast(
            SessionBackendHost, SeenStateHost()
        ),
    )

    result = await server._dispatch_backend_host_operation(
        "session/markAsSeen", {"sessionId": "saved-session"}
    )

    assert received == SessionMarkAsSeenParams(session_id="saved-session")
    assert result is not None
    assert result.response == EmptyResponse()


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


def _accept_session_backend(backend: SessionBackend) -> None:
    pass


def _accept_session_backend_host(backend: SessionBackendHost) -> None:
    pass


def _protocol_members(protocol: type[object]) -> set[str]:
    return {name for name in protocol.__dict__ if not name.startswith("_")}
