from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Never

import pytest

from vibe.app_server._legacy_composition import create_legacy_app_server
from vibe.app_server._mcp_auth import MCPAuthenticationService
from vibe.app_server._runtime import RootOpenRequest
from vibe.app_server.client import AppServerClient
from vibe.app_server.mcp_catalog import MCPCatalogService
from vibe.app_server.protocol import (
    ClientCapabilities,
    ClientInfo,
    MCPAddParams,
    MCPLoginParams,
    Notification,
)
from vibe.app_server.transport import memory_transport_pair
from vibe.core.config import MCPHttp, MCPStreamableHttp, build_user_config_orchestrator


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled", [None, "mcp/authUrl", "mcp_catalog/authUrl"])
async def test_sessionless_login_delivers_auth_url_before_login_completes(
    monkeypatch: pytest.MonkeyPatch, disabled: str | None
) -> None:
    authorized = asyncio.Event()

    async def login(
        server: MCPHttp | MCPStreamableHttp,
        *,
        on_url: Callable[[str], Awaitable[None]],
        headers: Mapping[str, str] | None = None,
    ) -> None:
        await on_url("https://auth.example.invalid/login")
        await authorized.wait()

    async def open_root(_request: RootOpenRequest) -> Never:
        raise AssertionError("MCP login must not create a session")

    monkeypatch.setattr("vibe.app_server._mcp_auth.perform_oauth_login", login)
    client_transport, server_transport = memory_transport_pair()
    server = create_legacy_app_server(
        server_transport,
        open_root=open_root,
        mcp_catalog_service=MCPCatalogService(
            MCPAuthenticationService(),
            sessionless_catalog_factory=build_user_config_orchestrator,
        ),
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    incoming = client.incoming()
    pending_login = None
    try:
        await client.initialize(
            ClientInfo(name="test", version="1"),
            ClientCapabilities(disabled_notifications=[disabled] if disabled else []),
        )
        await client.notify("initialized")
        await client.request(
            "mcp_catalog/add",
            MCPAddParams(url="https://example.invalid/mcp", name="example"),
        )
        pending_login = asyncio.create_task(
            client.request("mcp_catalog/login", MCPLoginParams(name="example"))
        )
        for method in ("mcp_catalog/authUrl", "mcp/authUrl"):
            if method == disabled:
                continue
            event = await asyncio.wait_for(anext(incoming), timeout=1)
            assert isinstance(event, Notification)
            assert event.method == method
            assert event.params == {
                "name": "example",
                "url": "https://auth.example.invalid/login",
            }
        assert not pending_login.done()
        authorized.set()
        assert await asyncio.wait_for(pending_login, timeout=1) == {"runtime": None}
    finally:
        authorized.set()
        await incoming.aclose()
        await client.close()
        if pending_login is not None:
            await asyncio.gather(pending_login, return_exceptions=True)
