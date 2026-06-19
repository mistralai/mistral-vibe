from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import keyring
from keyring.backend import KeyringBackend
import keyring.errors
from mcp.client.auth import OAuthFlowError
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
import pytest

from vibe.core.auth.mcp_oauth import (
    Fingerprint,
    KeyringTokenStorage,
    MCPOAuthLoginFailed,
)
from vibe.core.config import MCPOAuth, MCPStreamableHttp
from vibe.core.tools.base import BaseToolConfig, InvokeContext, ToolError
from vibe.core.tools.mcp import AuthStatus, MCPRegistry, MCPToolResult, RemoteTool
from vibe.core.tools.mcp.tools import (
    MCPHttpOAuthRuntime,
    _OpenArgs,
    create_mcp_http_proxy_tool_class,
)


class MemoryKeyring(KeyringBackend):
    priority: ClassVar[Any] = 100

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.store:
            raise keyring.errors.PasswordDeleteError(username)
        del self.store[(service, username)]


@pytest.fixture
def memory_keyring() -> Iterator[MemoryKeyring]:
    original = keyring.get_keyring()
    fake = MemoryKeyring()
    keyring.set_keyring(fake)
    try:
        yield fake
    finally:
        keyring.set_keyring(original)


def _oauth_server(
    *,
    name: str = "linear",
    url: str = "https://mcp.example.com/mcp",
    scopes: list[str] | None = None,
) -> MCPStreamableHttp:
    return MCPStreamableHttp(
        name=name,
        transport="streamable-http",
        url=url,
        auth=MCPOAuth(type="oauth", scopes=scopes if scopes is not None else ["read"]),
    )


async def _save_valid_oauth_state(srv: MCPStreamableHttp) -> None:
    storage = KeyringTokenStorage(alias=srv.name)
    await storage.set_tokens(
        OAuthToken(
            access_token="ACCESS",
            token_type="Bearer",
            expires_in=3600,
            refresh_token="REFRESH",
        )
    )
    await Fingerprint.compute(srv).save(srv.name)


class TestMCPRegistryOAuthDiscovery:
    @pytest.mark.asyncio
    async def test_missing_tokens_mark_needs_auth_without_caching(
        self, memory_keyring: MemoryKeyring
    ) -> None:
        srv = _oauth_server()
        registry = MCPRegistry()

        tools = await registry.get_tools_async([srv])

        assert tools == {}
        assert registry.needs_auth == {"linear"}
        assert registry.status()["linear"] == AuthStatus.NEEDS_AUTH
        assert registry.count_loaded([srv]) == 0

    @pytest.mark.asyncio
    async def test_removed_oauth_server_clears_auth_status(
        self, memory_keyring: MemoryKeyring
    ) -> None:
        srv = _oauth_server()
        registry = MCPRegistry()

        await registry.get_tools_async([srv])
        await registry.get_tools_async([])

        assert registry.needs_auth == set()
        assert registry.status() == {}

    @pytest.mark.asyncio
    async def test_valid_tokens_register_tools_with_oauth_provider(
        self, memory_keyring: MemoryKeyring
    ) -> None:
        srv = _oauth_server()
        await _save_valid_oauth_state(srv)
        registry = MCPRegistry()
        remote = RemoteTool(name="create_issue")

        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http",
            new=AsyncMock(return_value=[remote]),
        ) as list_tools:
            tools = await registry.get_tools_async([srv])

        assert "linear_create_issue" in tools
        assert registry.needs_auth == set()
        assert registry.status()["linear"] == AuthStatus.OK
        await_args = list_tools.await_args
        assert await_args is not None
        assert await_args.kwargs["auth"] is not None

    @pytest.mark.asyncio
    async def test_fingerprint_mismatch_deletes_tokens_and_requires_auth(
        self, memory_keyring: MemoryKeyring
    ) -> None:
        original = _oauth_server(scopes=["read"])
        changed = _oauth_server(scopes=["read", "write"])
        await _save_valid_oauth_state(original)
        storage = KeyringTokenStorage(alias="linear")
        await storage.set_client_info(
            OAuthClientInformationFull.model_validate({
                "client_id": "client",
                "redirect_uris": ["http://127.0.0.1:47823/callback"],
                "token_endpoint_auth_method": "none",
            })
        )
        registry = MCPRegistry()

        tools = await registry.get_tools_async([changed])

        assert tools == {}
        assert registry.needs_auth == {"linear"}
        assert await storage.get_tokens() is None
        assert await storage.get_client_info() is None
        assert await Fingerprint.load("linear") is None

    @pytest.mark.asyncio
    async def test_login_clears_needs_auth_and_rediscovers(
        self, memory_keyring: MemoryKeyring
    ) -> None:
        srv = _oauth_server()
        registry = MCPRegistry()
        await registry.get_tools_async([srv])

        async def perform_login(_srv: MCPStreamableHttp, *, on_url: object) -> None:
            await _save_valid_oauth_state(_srv)

        with (
            patch(
                "vibe.core.tools.mcp.registry.perform_oauth_login",
                new=AsyncMock(side_effect=perform_login),
            ),
            patch(
                "vibe.core.tools.mcp.registry.list_tools_http",
                new=AsyncMock(return_value=[RemoteTool(name="search")]),
            ),
        ):
            await registry.login("linear", on_url=AsyncMock())

        assert registry.needs_auth == set()
        assert registry.status()["linear"] == AuthStatus.OK
        tools = await registry.get_tools_async([srv])
        assert "linear_search" in tools

    @pytest.mark.asyncio
    async def test_login_keeps_needs_auth_when_discovery_fails(
        self, memory_keyring: MemoryKeyring
    ) -> None:
        srv = _oauth_server()
        registry = MCPRegistry()
        await registry.get_tools_async([srv])

        async def perform_login(_srv: MCPStreamableHttp, *, on_url: object) -> None:
            await _save_valid_oauth_state(_srv)

        with (
            patch(
                "vibe.core.tools.mcp.registry.perform_oauth_login",
                new=AsyncMock(side_effect=perform_login),
            ),
            patch(
                "vibe.core.tools.mcp.registry.list_tools_http",
                new=AsyncMock(side_effect=RuntimeError("boom")),
            ),
        ):
            with pytest.raises(MCPOAuthLoginFailed):
                await registry.login("linear", on_url=AsyncMock())

        assert registry.needs_auth == {"linear"}
        assert registry.status()["linear"] == AuthStatus.NEEDS_AUTH

    @pytest.mark.asyncio
    async def test_mark_oauth_failure_drops_cached_tools(
        self, memory_keyring: MemoryKeyring
    ) -> None:
        srv = _oauth_server()
        await _save_valid_oauth_state(srv)
        registry = MCPRegistry()

        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http",
            new=AsyncMock(return_value=[RemoteTool(name="search")]),
        ):
            tools = await registry.get_tools_async([srv])

        assert "linear_search" in tools
        assert registry.count_loaded([srv]) == 1

        await registry.mark_oauth_failure("linear")

        assert registry.needs_auth == {"linear"}
        assert registry.count_loaded([srv]) == 0

    @pytest.mark.asyncio
    async def test_logout_deletes_tokens_client_info_and_fingerprint(
        self, memory_keyring: MemoryKeyring
    ) -> None:
        srv = _oauth_server()
        await _save_valid_oauth_state(srv)
        storage = KeyringTokenStorage(alias="linear")
        await storage.set_client_info(
            OAuthClientInformationFull.model_validate({
                "client_id": "client",
                "redirect_uris": ["http://127.0.0.1:47823/callback"],
                "token_endpoint_auth_method": "none",
            })
        )
        registry = MCPRegistry()
        with patch(
            "vibe.core.tools.mcp.registry.list_tools_http",
            new=AsyncMock(return_value=[RemoteTool(name="search")]),
        ):
            await registry.get_tools_async([srv])

        await registry.logout("linear")

        assert registry.needs_auth == {"linear"}
        assert await storage.get_tokens() is None
        assert await Fingerprint.load("linear") is None
        assert await storage.get_client_info() is None


class TestMCPHttpOAuthProxy:
    @pytest.mark.asyncio
    async def test_oauth_flow_error_marks_auth_failure_with_stop_turn_message(
        self,
    ) -> None:
        callback = AsyncMock()
        tool_cls = create_mcp_http_proxy_tool_class(
            url="https://mcp.example.com/mcp",
            remote=RemoteTool(name="search"),
            alias="linear",
            oauth_runtime=MCPHttpOAuthRuntime(
                lock=asyncio.Lock(), failure_callback=callback
            ),
        )
        tool = tool_cls.from_config(lambda: BaseToolConfig())

        with patch(
            "vibe.core.tools.mcp.tools.call_tool_http",
            new=AsyncMock(side_effect=OAuthFlowError("invalid_grant")),
        ):
            with pytest.raises(ToolError, match="lost authentication"):
                async for _ in tool.run(_OpenArgs(), InvokeContext(tool_call_id="tc")):
                    pass

        callback.assert_awaited_once_with("linear")

    @pytest.mark.asyncio
    async def test_oauth_lock_serializes_concurrent_calls(self) -> None:
        active = 0
        max_active = 0

        async def fake_call(*_args: object, **_kwargs: object) -> MCPToolResult:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return MCPToolResult(server="linear", tool="search")

        tool_cls = create_mcp_http_proxy_tool_class(
            url="https://mcp.example.com/mcp",
            remote=RemoteTool(name="search"),
            alias="linear",
            oauth_runtime=MCPHttpOAuthRuntime(
                lock=asyncio.Lock(), failure_callback=AsyncMock()
            ),
        )

        async def run_once() -> None:
            tool = tool_cls.from_config(lambda: BaseToolConfig())
            async for _ in tool.run(_OpenArgs(), InvokeContext(tool_call_id="tc")):
                pass

        with patch("vibe.core.tools.mcp.tools.call_tool_http", new=fake_call):
            await asyncio.gather(run_once(), run_once())

        assert max_active == 1
