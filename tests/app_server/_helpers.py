"""Shared helpers for the session-less ``setup/*`` wire tests."""

from __future__ import annotations

from vibe.app_server._runtime import create_harness_server
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import ClientInfo
from vibe.app_server.transport import JsonRpcTransport, memory_transport_pair


async def setup_client(
    *, entrypoint: str = "unknown"
) -> tuple[AppServerClient, JsonRpcTransport, JsonRpcTransport]:
    """An initialized pre-session connection; opening a root fails the test."""
    client_transport, server_transport = memory_transport_pair()

    server = await create_harness_server(server_transport, transport_kind="in_process")
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(
        ClientInfo(name="setup-test", version="1", entrypoint=entrypoint)  # type: ignore[arg-type]
    )
    await client.notify("initialized")
    return client, client_transport, server_transport
