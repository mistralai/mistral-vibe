from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

from pydantic import ValidationError
import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.app_server import build_test_app_server, legacy_backend
from vibe.app_server._dispatch import DispatchResult
from vibe.app_server._session_backend_port import SessionBackendError
from vibe.app_server.client import AppServerClient, AppServerConnectionClosed
from vibe.app_server.protocol import (
    AppServerResponseError,
    CallbackCallResponse,
    ClientCapabilities,
    ClientInfo,
    JsonPatchOperation,
    JsonRpcErrorResponse,
    JsonRpcProtocolError,
    ProtocolError,
    ProtocolErrorCode,
    ServerRequest,
    SessionUpdatedParams,
    validate_json_rpc_envelope,
)
from vibe.app_server.server import CallbackDelivery, InitializationState
from vibe.app_server.transport import memory_transport_pair
from vibe.core.config import SessionLoggingConfig


@pytest.mark.parametrize(
    "message",
    [
        {"jsonrpc": "2.0", "id": "client-1"},
        {
            "jsonrpc": "2.0",
            "id": "client-1",
            "result": {},
            "error": {"code": "internal_error", "message": "failed"},
        },
        {"jsonrpc": "2.0", "method": "initialized", "params": {}, "extra": True},
        {"jsonrpc": "2.0", "id": True, "result": {}},
    ],
)
def test_json_rpc_envelopes_reject_malformed_shapes(message: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        validate_json_rpc_envelope(message)


@pytest.mark.asyncio
async def test_client_rejects_snake_case_response_fields() -> None:
    client_transport, peer_transport = memory_transport_pair()
    client = AppServerClient(client_transport)
    initialize = asyncio.create_task(
        client.initialize(ClientInfo(name="test", version="1"))
    )
    request = await anext(peer_transport.messages())

    await peer_transport.send({
        "jsonrpc": "2.0",
        "id": request["id"],
        "result": {"server_info": {"name": "test-server", "version": "1"}},
    })

    with pytest.raises(ValidationError):
        await initialize

    await client.close()
    await peer_transport.close()


@pytest.mark.asyncio
async def test_client_rejects_unknown_response_id() -> None:
    client_transport, peer_transport = memory_transport_pair()
    client = AppServerClient(client_transport)
    pending = asyncio.create_task(client.request("test/wait"))
    await anext(peer_transport.messages())

    await peer_transport.send({"jsonrpc": "2.0", "id": "client-unknown", "result": {}})

    with pytest.raises(AppServerConnectionClosed) as exc_info:
        await pending

    assert isinstance(exc_info.value.__cause__, JsonRpcProtocolError)
    await client.close()
    await peer_transport.close()


@pytest.mark.asyncio
async def test_client_accepts_late_response_to_cancelled_request() -> None:
    client_transport, peer_transport = memory_transport_pair()
    client = AppServerClient(client_transport)
    cancelled = asyncio.create_task(client.request("test/cancel"))
    cancelled_request = await anext(peer_transport.messages())
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled

    await peer_transport.send({
        "jsonrpc": "2.0",
        "id": cancelled_request["id"],
        "result": {},
    })
    active = asyncio.create_task(client.request("test/active"))
    active_request = await anext(peer_transport.messages())
    await peer_transport.send({
        "jsonrpc": "2.0",
        "id": active_request["id"],
        "result": {"accepted": True},
    })

    assert await active == {"accepted": True}
    assert client._abandoned_request_ids == set()
    await client.close()
    await peer_transport.close()


@pytest.mark.asyncio
async def test_response_boundary_is_ordered_without_blocking_nested_requests() -> None:
    client_transport, peer_transport = memory_transport_pair()
    client = AppServerClient(client_transport)
    observed: list[str] = []

    async def consume() -> None:
        incoming = client.incoming()
        before = await anext(incoming)
        observed.append(before.method)
        assert await client.request("test/resync") == {"resynced": True}
        observed.append("resynced")
        after = await anext(incoming)
        observed.append(after.method)
        await incoming.aclose()

    consumer = asyncio.create_task(consume())
    request = asyncio.create_task(
        client.request(
            "test/resume", response_boundary=lambda _result: observed.append("adopted")
        )
    )
    resume_request = await anext(peer_transport.messages())
    await peer_transport.send({"jsonrpc": "2.0", "method": "test/before", "params": {}})
    await peer_transport.send({
        "jsonrpc": "2.0",
        "id": resume_request["id"],
        "result": {"resumed": True},
    })

    nested_request = await asyncio.wait_for(anext(peer_transport.messages()), timeout=1)
    assert nested_request["method"] == "test/resync"
    await peer_transport.send({
        "jsonrpc": "2.0",
        "id": nested_request["id"],
        "result": {"resynced": True},
    })
    await peer_transport.send({"jsonrpc": "2.0", "method": "test/after", "params": {}})

    assert await asyncio.wait_for(request, timeout=1) == {"resumed": True}
    await asyncio.wait_for(consumer, timeout=1)
    assert observed == ["test/before", "resynced", "adopted", "test/after"]
    await client.close()
    await peer_transport.close()


@pytest.mark.asyncio
async def test_server_rejects_unknown_response_id() -> None:
    client_transport, server_transport = memory_transport_pair()
    agent_loop = build_test_agent_loop()
    server = build_test_app_server(agent_loop, server_transport)
    serving = asyncio.create_task(server.serve())

    await client_transport.send({"jsonrpc": "2.0", "id": 99, "result": {}})

    with pytest.raises(
        JsonRpcProtocolError,
        match="Response does not match a pending server request: 99",
    ):
        await serving

    await client_transport.close()
    await agent_loop.aclose()


@pytest.mark.asyncio
async def test_late_callback_delivery_error_is_ignored_after_semantic_answer() -> None:
    client_transport, server_transport = memory_transport_pair()
    agent_loop = build_test_agent_loop()
    server = build_test_app_server(agent_loop, server_transport)
    server._callback_requests[7] = CallbackDelivery(
        session_id=agent_loop.session_id, callback_id="callback-1"
    )

    await server._after_response(
        ServerRequest(
            id="callback-result",
            method="callback/result",
            params={"result": {"callbackId": "callback-1"}},
        ),
        DispatchResult(response=CallbackCallResponse(callback_id="callback-1")),
    )
    assert server._callback_requests[7].answered

    await server._handle_response(
        JsonRpcErrorResponse(
            id=7,
            error=ProtocolError(
                code=ProtocolErrorCode.INTERNAL_ERROR, message="delivery failed"
            ),
        )
    )

    assert server._callback_requests == {}
    await server.close()
    await client_transport.close()
    await agent_loop.aclose()


@pytest.mark.asyncio
async def test_failed_compact_releases_deferred_events_after_its_error_response() -> (
    None
):
    """*Prepare*: A compact backend that has a failed checkpoint buffered behind its error response.
    *Do*: Dispatch the compact request.
    *Assert*: The backend releases the checkpoint only once the error is written.
    """

    class FailingCompactBackend:
        session_id = "session-1"

        def guard_request(self) -> None:
            return None

        async def compact(self, _params: object) -> object:
            raise SessionBackendError(
                ProtocolErrorCode.COMPACTION_FAILED,
                "Context compaction failed",
                after_response=release_events,
            )

    # Prepare
    client_transport, server_transport = memory_transport_pair()
    agent_loop = build_test_agent_loop()
    server = build_test_app_server(agent_loop, server_transport)
    release_events = Mock()
    server._root = cast(Any, FailingCompactBackend())
    server._initialization = InitializationState.INITIALIZED

    # Do
    await server._handle_request_once(
        ServerRequest(
            id="compact", method="session/compact", params={"sessionId": "session-1"}
        )
    )

    # Assert
    response = await anext(client_transport.messages())
    assert response["error"]["code"] == ProtocolErrorCode.COMPACTION_FAILED
    release_events.assert_called_once_with()
    server._root = None
    await server.close()
    await client_transport.close()
    await agent_loop.aclose()


@pytest.mark.asyncio
async def test_response_write_failure_abandons_deferred_backend_work() -> None:
    """*Prepare*: A successful backend result whose response write will fail.
    *Do*: Dispatch that result through the server.
    *Assert*: The abandonment callback releases the backend reservation.
    """
    # Prepare
    client_transport, server_transport = memory_transport_pair()
    agent_loop = build_test_agent_loop()
    server = build_test_app_server(agent_loop, server_transport)
    abandon = Mock()

    async def dispatch(_request: ServerRequest) -> DispatchResult:
        return DispatchResult(
            response=CallbackCallResponse(callback_id="callback-1"),
            on_response_abandoned=abandon,
        )

    server._dispatch_or_error = dispatch  # type: ignore[method-assign]
    server._send = AsyncMock(side_effect=ConnectionError("connection closed"))

    # Do / Assert
    with pytest.raises(ConnectionError, match="connection closed"):
        await server._handle_request_once(
            ServerRequest(id="result", method="test/result", params={})
        )
    abandon.assert_called_once_with()
    await server.close()
    await client_transport.close()
    await agent_loop.aclose()


@pytest.mark.asyncio
async def test_failed_compact_response_write_failure_abandons_deferred_events() -> None:
    """*Prepare*: A failed compact whose error response cannot be written.
    *Do*: Dispatch the compact request.
    *Assert*: The backend discards the undeliverable checkpoint and releases its gate.
    """

    class FailingCompactBackend:
        session_id = "session-1"

        def guard_request(self) -> None:
            return None

        async def compact(self, _params: object) -> object:
            raise SessionBackendError(
                ProtocolErrorCode.COMPACTION_FAILED,
                "Context compaction failed",
                on_response_abandoned=abandon_events,
            )

    # Prepare
    client_transport, server_transport = memory_transport_pair()
    agent_loop = build_test_agent_loop()
    server = build_test_app_server(agent_loop, server_transport)
    abandon_events = Mock()
    server._root = cast(Any, FailingCompactBackend())
    server._initialization = InitializationState.INITIALIZED
    server._send = AsyncMock(side_effect=ConnectionError("connection closed"))

    # Do / Assert
    with pytest.raises(ConnectionError, match="connection closed"):
        await server._handle_request_once(
            ServerRequest(
                id="compact",
                method="session/compact",
                params={"sessionId": "session-1"},
            )
        )
    abandon_events.assert_called_once_with()
    server._root = None
    await server.close()
    await client_transport.close()
    await agent_loop.aclose()


@pytest.mark.asyncio
async def test_projection_notifications_cannot_be_disabled() -> None:
    client_transport, server_transport = memory_transport_pair()
    agent_loop = build_test_agent_loop()
    server = build_test_app_server(agent_loop, server_transport)
    server._client_capabilities = ClientCapabilities(
        disabled_notifications=["session/updated"]
    )
    server._connection_attached = True

    await server._notify(
        "session/updated",
        SessionUpdatedParams(
            event_id=0,
            session_id=agent_loop.session_id,
            emitted_at=1,
            patch=[JsonPatchOperation(op="replace", path="/updatedAt", value=2)],
        ),
    )
    notification = await anext(client_transport.messages())

    assert notification["method"] == "session/updated"
    assert notification["params"]["eventId"] == 1
    await server.close()
    await client_transport.close()
    await agent_loop.aclose()


@pytest.mark.asyncio
async def test_unknown_method_before_session_start_is_method_not_found() -> None:
    client_transport, server_transport = memory_transport_pair()
    agent_loop = build_test_agent_loop()
    server = build_test_app_server(agent_loop, server_transport)
    client = AppServerClient(client_transport, run_peer=server.serve)
    try:
        await client.initialize(ClientInfo(name="test", version="1"))
        await client.notify("initialized")

        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request("unknown/method")

        assert exc_info.value.error.code is ProtocolErrorCode.METHOD_NOT_FOUND
        assert server._root is None
    finally:
        await client.close()
        await agent_loop.aclose()


@pytest.mark.asyncio
async def test_shutdown_closes_root_and_transport_after_child_cleanup_failure() -> None:
    client_transport, server_transport = memory_transport_pair()
    agent_loop = build_test_agent_loop()
    server = build_test_app_server(agent_loop, server_transport)
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="test", version="1"))
    await client.notify("initialized")
    await client.request("session/start", {"agentConfig": {"cwd": str(agent_loop.cwd)}})
    backend = legacy_backend(server)
    handler = backend.handler
    handler.close = AsyncMock()
    backend.children.close = AsyncMock(side_effect=RuntimeError("child close failed"))
    agent_loop.emit_session_closed_telemetry = Mock()
    agent_loop.aclose = AsyncMock()
    agent_loop.telemetry_client.aclose = AsyncMock()

    with pytest.raises(RuntimeError, match="child close failed"):
        await server.close()

    handler.close.assert_awaited_once()
    agent_loop.emit_session_closed_telemetry.assert_called_once_with()
    agent_loop.aclose.assert_awaited_once()
    agent_loop.telemetry_client.aclose.assert_awaited_once()
    with pytest.raises(RuntimeError, match="closed"):
        await server_transport.send({"jsonrpc": "2.0", "method": "test"})
    await client.close()


@pytest.mark.asyncio
async def test_session_close_records_pointer_before_responding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client_transport, server_transport = memory_transport_pair()
    agent_loop = build_test_agent_loop(
        config=build_test_vibe_config(
            session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
        )
    )
    await agent_loop.persist_empty_session()
    server = build_test_app_server(agent_loop, server_transport)
    client = AppServerClient(
        client_transport,
        run_peer=lambda: server.serve_connection(
            server_transport, close_on_disconnect=False
        ),
    )
    record = Mock()
    monkeypatch.setattr(
        "vibe.app_server._legacy_session_runtime.last_session_pointer.record", record
    )

    await client.initialize(ClientInfo(name="test", version="1"))
    await client.notify("initialized")
    await client.request("session/start", {"agentConfig": {"cwd": str(agent_loop.cwd)}})
    await client.request("session/stop", {"sessionId": agent_loop.session_id})

    record.assert_called_once_with(
        agent_loop.config.session_logging, agent_loop.session_id
    )
    assert server._root is None
    await client.close()


@pytest.mark.asyncio
async def test_session_close_does_not_record_unpersisted_pointer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_transport, server_transport = memory_transport_pair()
    agent_loop = build_test_agent_loop()
    server = build_test_app_server(agent_loop, server_transport)
    client = AppServerClient(
        client_transport,
        run_peer=lambda: server.serve_connection(
            server_transport, close_on_disconnect=False
        ),
    )
    record = Mock()
    monkeypatch.setattr(
        "vibe.app_server._legacy_session_runtime.last_session_pointer.record", record
    )

    await client.initialize(ClientInfo(name="test", version="1"))
    await client.notify("initialized")
    await client.request("session/start", {"agentConfig": {"cwd": str(agent_loop.cwd)}})
    await client.request("session/stop", {"sessionId": agent_loop.session_id})

    record.assert_not_called()
    await client.close()
