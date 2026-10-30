from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest

from vibe.app_server._host import HostRequestHandler
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._session_backend_port import SessionBackendHost
from vibe.app_server._session_peers import SessionPeerError
from vibe.app_server._session_spawn import SessionSpawnError
from vibe.app_server.protocol import (
    SESSION_PEER_READ_METHOD,
    SESSION_PEER_SEND_METHOD,
    SESSION_SPAWN_METHOD,
    ClientCapabilities,
    JsonRpcErrorResponse,
    JsonRpcSuccessResponse,
    ProtocolError,
    ProtocolErrorCode,
    SessionPeerReadParams,
    SessionPeerReadResponse,
    SessionPeerSendParams,
    SessionSpawnNewWorktree,
    SessionSpawnParams,
    SessionSpawnResponse,
)
from vibe.app_server.server import AppServer
from vibe.app_server.transport import JsonRpcTransport, memory_transport_pair
from vibe.core.config.harness_files import HarnessFilesManager

_PARAMS = SessionSpawnParams(
    parent_session_id="parent-session",
    prompt="Fix the flaky test",
    title="Flaky test",
    checkout=SessionSpawnNewWorktree(),
    idempotency_key="call-1",
    model="devstral",
    reasoning_effort="high",
    approval_policy="ask",
)


def _attached_server(
    *,
    session_spawn: bool = True,
    session_peers: bool = False,
    process_services: ProcessServices,
) -> tuple[AppServer, AsyncIterator[dict[str, Any]]]:
    client_transport, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport,
        session_backend_host_factory=lambda _: cast(SessionBackendHost, object()),
        host_handler=HostRequestHandler(
            HarnessFilesManager(sources=("user", "project")),
            org_config=process_services.org_config,
        ),
    )
    # A session is attached and its client finished the handshake.
    server._client_capabilities = ClientCapabilities(
        session_spawn=session_spawn, session_peers=session_peers
    )
    server._connection_attached = True
    return server, _messages(client_transport)


async def _messages(transport: JsonRpcTransport) -> AsyncIterator[dict[str, Any]]:
    async for message in transport.messages():
        yield message


async def _next(messages: AsyncIterator[dict[str, Any]]) -> dict[str, Any]:
    return await asyncio.wait_for(anext(messages), timeout=1)


@pytest.mark.asyncio
async def test_a_spawn_asks_the_client_and_returns_its_answer(
    process_services: ProcessServices,
) -> None:
    server, messages = _attached_server(process_services=process_services)

    spawning = asyncio.create_task(server.spawn_session(_PARAMS))
    request = await _next(messages)

    assert request["method"] == SESSION_SPAWN_METHOD
    assert request["params"] == {
        "parentSessionId": "parent-session",
        "prompt": "Fix the flaky test",
        "title": "Flaky test",
        "checkout": {"kind": "new_worktree"},
        "idempotencyKey": "call-1",
        "model": "devstral",
        "reasoningEffort": "high",
        "approvalPolicy": "ask",
    }
    await server._handle_response(
        JsonRpcSuccessResponse(
            id=request["id"],
            result={
                "sessionId": "spawned-1",
                "title": "Flaky test",
                "url": "/code/project/spawned-1",
            },
        )
    )
    assert await spawning == SessionSpawnResponse(
        session_id="spawned-1", title="Flaky test", url="/code/project/spawned-1"
    )
    assert server._client_requests == {}


@pytest.mark.asyncio
async def test_a_client_refusal_fails_the_spawn_with_its_message(
    process_services: ProcessServices,
) -> None:
    server, messages = _attached_server(process_services=process_services)

    spawning = asyncio.create_task(server.spawn_session(_PARAMS))
    request = await _next(messages)
    await server._handle_response(
        JsonRpcErrorResponse(
            id=request["id"],
            error=ProtocolError(
                code=ProtocolErrorCode.INVALID_PARAMS,
                message="The project is not linked on this computer",
            ),
        )
    )

    with pytest.raises(SessionSpawnError, match="not linked on this computer"):
        await spawning


@pytest.mark.asyncio
async def test_a_malformed_answer_fails_the_spawn_not_the_connection(
    process_services: ProcessServices,
) -> None:
    server, messages = _attached_server(process_services=process_services)

    spawning = asyncio.create_task(server.spawn_session(_PARAMS))
    request = await _next(messages)
    await server._handle_response(
        JsonRpcSuccessResponse(id=request["id"], result={"unexpected": True})
    )

    with pytest.raises(SessionSpawnError, match="invalidly"):
        await spawning


@pytest.mark.asyncio
async def test_a_client_that_cannot_spawn_is_never_asked(
    process_services: ProcessServices,
) -> None:
    server, _ = _attached_server(session_spawn=False, process_services=process_services)

    with pytest.raises(SessionSpawnError, match="cannot start sessions"):
        await server.spawn_session(_PARAMS)
    assert server._client_requests == {}


@pytest.mark.asyncio
async def test_a_pending_spawn_fails_as_unknown_when_the_client_goes_away(
    process_services: ProcessServices,
) -> None:
    """The client may have created the session before it went away, so the
    model must not take the failure as "not started" and start it again.
    """
    server, messages = _attached_server(process_services=process_services)

    spawning = asyncio.create_task(server.spawn_session(_PARAMS))
    await _next(messages)
    server._abandon_client_requests("The client disconnected")

    with pytest.raises(SessionSpawnError, match="may or may not have started"):
        await spawning
    assert server._client_requests == {}


@pytest.mark.asyncio
async def test_a_cancelled_spawn_leaves_no_pending_request(
    process_services: ProcessServices,
) -> None:
    server, messages = _attached_server(process_services=process_services)

    spawning = asyncio.create_task(server.spawn_session(_PARAMS))
    await _next(messages)
    spawning.cancel()

    with pytest.raises(asyncio.CancelledError):
        await spawning
    assert server._client_requests == {}


@pytest.mark.asyncio
async def test_an_answer_after_the_tool_gave_up_is_dropped(
    process_services: ProcessServices,
) -> None:
    server, messages = _attached_server(process_services=process_services)
    spawning = asyncio.create_task(server.spawn_session(_PARAMS))
    request = await _next(messages)
    spawning.cancel()
    with pytest.raises(asyncio.CancelledError):
        await spawning

    await server._handle_response(
        JsonRpcSuccessResponse(id=request["id"], result={"sessionId": "late"})
    )

    assert server._unanswered_client_requests == {}


_SEND = SessionPeerSendParams(
    target_session_id="other", text="Rebase", idempotency_key="k-1"
)


@pytest.mark.asyncio
async def test_a_peer_read_asks_the_client_and_returns_its_answer(
    process_services: ProcessServices,
) -> None:
    server, messages = _attached_server(
        session_peers=True, process_services=process_services
    )

    reading = asyncio.create_task(
        server.read_peer_session(
            SessionPeerReadParams(
                target_session_id="other", limit=5, max_message_chars=4000
            )
        )
    )
    request = await _next(messages)

    assert request["method"] == SESSION_PEER_READ_METHOD
    assert request["params"] == {
        "targetSessionId": "other",
        "limit": 5,
        "includeToolCalls": False,
        "maxMessageChars": 4000,
    }
    await server._handle_response(
        JsonRpcSuccessResponse(
            id=request["id"],
            result={
                "sessionId": "other",
                "status": "waiting_for_user",
                "statusDetail": "Approve bash",
                "messages": [{"role": "assistant", "text": "May I run it?"}],
            },
        )
    )
    answer = await reading
    assert isinstance(answer, SessionPeerReadResponse)
    assert answer.status == "waiting_for_user"
    assert server._client_requests == {}


@pytest.mark.asyncio
async def test_a_client_that_hosts_no_peers_is_never_asked(
    process_services: ProcessServices,
) -> None:
    server, _ = _attached_server(session_peers=False, process_services=process_services)

    with pytest.raises(SessionPeerError, match="cannot reach other sessions"):
        await server.send_to_peer_session(_SEND)
    assert server._client_requests == {}


@pytest.mark.asyncio
async def test_a_peer_refusal_fails_the_request_not_the_connection(
    process_services: ProcessServices,
) -> None:
    server, messages = _attached_server(
        session_peers=True, process_services=process_services
    )

    sending = asyncio.create_task(server.send_to_peer_session(_SEND))
    request = await _next(messages)
    assert request["method"] == SESSION_PEER_SEND_METHOD
    await server._handle_response(
        JsonRpcErrorResponse(
            id=request["id"],
            error=ProtocolError(
                code=ProtocolErrorCode.INVALID_PARAMS,
                message="No session other in this project",
            ),
        )
    )

    with pytest.raises(SessionPeerError, match="No session other"):
        await sending


@pytest.mark.asyncio
async def test_a_pending_send_fails_as_unknown_when_the_client_goes_away(
    process_services: ProcessServices,
) -> None:
    server, messages = _attached_server(
        session_peers=True, process_services=process_services
    )

    sending = asyncio.create_task(server.send_to_peer_session(_SEND))
    await _next(messages)
    server._abandon_client_requests("The client disconnected")

    with pytest.raises(SessionPeerError, match="may or may not have been sent"):
        await sending
