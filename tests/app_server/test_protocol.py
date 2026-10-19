from __future__ import annotations

import asyncio

from pydantic import ValidationError
import pytest

from tests.app_server._helpers import setup_client
from vibe.app_server.client import AppServerClient, AppServerConnectionClosed
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientInfo,
    JsonRpcProtocolError,
    ProtocolErrorCode,
    validate_json_rpc_envelope,
)
from vibe.app_server.transport import memory_transport_pair


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["tools/list", "stats/read", "diagnostics/list"])
async def test_retired_rpc_returns_method_not_found(method: str) -> None:
    client, client_transport, server_transport = await setup_client()
    try:
        with pytest.raises(AppServerResponseError) as error:
            await client.request(method)
        assert error.value.error.code == ProtocolErrorCode.METHOD_NOT_FOUND
    finally:
        await client.close()
        await client_transport.close()
        await server_transport.close()


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
