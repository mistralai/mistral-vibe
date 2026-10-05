"""Shared helpers for the session-less ``setup/*`` wire tests."""

from __future__ import annotations

from typing import Never

from vibe.app_server._legacy_composition import create_legacy_app_server
from vibe.app_server._runtime import RootOpenRequest
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import ClientInfo
from vibe.app_server.transport import JsonRpcTransport, memory_transport_pair


async def setup_client(
    *, entrypoint: str = "unknown"
) -> tuple[AppServerClient, JsonRpcTransport, JsonRpcTransport]:
    """An initialized pre-session connection; opening a root fails the test."""
    client_transport, server_transport = memory_transport_pair()

    async def open_root(_request: RootOpenRequest) -> Never:
        raise AssertionError("setup/* must not open a session")

    server = create_legacy_app_server(server_transport, open_root=open_root)
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(
        ClientInfo(name="setup-test", version="1", entrypoint=entrypoint)  # type: ignore[arg-type]
    )
    await client.notify("initialized")
    return client, client_transport, server_transport
