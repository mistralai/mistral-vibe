from __future__ import annotations

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.app_server import create_test_app_server_session
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.app_server.models import MCPSourceStatus
from vibe.app_server.protocol import (
    AppServerResponseError,
    Notification,
    ProtocolErrorCode,
)
from vibe.core.config import MCPHttp, MCPOAuth, MCPStdio
from vibe.core.config.types import ConcurrencyConflictError


class LoginMCPRegistry(FakeMCPRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.login_calls: list[str] = []

    async def login(
        self, alias: str, *, on_url: Callable[[str], Awaitable[None]]
    ) -> None:
        self.login_calls.append(alias)
        await on_url("https://auth.example.com/oauth")


class FailingMCPRegistry(FakeMCPRegistry):
    """Registry that always fails discovery for a named server."""

    def __init__(self, failing_server: str) -> None:
        super().__init__()
        self._failing_server = failing_server

    async def get_tools_async(self, servers):
        working = [s for s in servers if s.name != self._failing_server]
        for s in servers:
            if s.name == self._failing_server:
                self._failed[s.name] = "connection refused"
        return await super().get_tools_async(working)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["add", "toggle"])
async def test_mcp_config_conflicts_are_public_conflicts(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    conflict = ConcurrencyConflictError("expected", "actual")
    target = "persist_oauth_mcp_server" if operation == "add" else "persist_mcp_toggle"
    monkeypatch.setattr(
        f"vibe.app_server._resources.{target}", AsyncMock(side_effect=conflict)
    )
    agent_loop = build_test_agent_loop()
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        with pytest.raises(AppServerResponseError) as exc_info:
            if operation == "add":
                await session.resources.mcp.add(
                    url="https://mcp.example.com/mcp",
                    name=None,
                    scopes=[],
                    transport="streamable-http",
                )
            else:
                await session.resources.mcp.toggle(
                    "search", source="server", disabled=True
                )
    finally:
        await session.close()

    assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT


@pytest.mark.asyncio
async def test_mcp_login_streams_typed_auth_url_notification() -> None:
    registry = LoginMCPRegistry()
    config = build_test_vibe_config(
        mcp_servers=[
            MCPHttp(
                name="search",
                transport="http",
                url="https://mcp.example.com",
                auth=MCPOAuth(type="oauth", scopes=[]),
            )
        ]
    )
    agent_loop = build_test_agent_loop(config=config, mcp_registry=registry)
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        events = [event async for event in session.resources.mcp.login("search")]
    finally:
        await session.close()

    assert registry.login_calls == ["search"]
    assert [(event.name, event.url) for event in events] == [
        ("search", "https://auth.example.com/oauth")
    ]


@pytest.mark.asyncio
async def test_unknown_notifications_do_not_enter_mcp_login_stream() -> None:
    agent_loop = build_test_agent_loop()
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        consumed = await session.resources.consume_notification(
            Notification(method="future/event", params={})
        )
    finally:
        await session.close()

    assert consumed is False


@pytest.mark.asyncio
async def test_mcp_toggle_enable_rediscovers_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vibe.app_server._resources.persist_mcp_toggle", AsyncMock())
    server = MCPStdio(name="search", transport="stdio", command="fake-cmd")
    config = build_test_vibe_config(mcp_servers=[server])
    agent_loop = build_test_agent_loop(config=config)
    refresh_mock = AsyncMock()
    agent_loop.tool_manager.refresh_remote_tools_async = refresh_mock
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        await session.resources.mcp.toggle("search", source="server", disabled=False)
    finally:
        await session.close()

    refresh_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_toggle_disable_does_not_rediscover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vibe.app_server._resources.persist_mcp_toggle", AsyncMock())
    server = MCPStdio(name="search", transport="stdio", command="fake-cmd")
    config = build_test_vibe_config(mcp_servers=[server])
    agent_loop = build_test_agent_loop(config=config)
    refresh_mock = AsyncMock()
    agent_loop.tool_manager.refresh_remote_tools_async = refresh_mock
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        await session.resources.mcp.toggle("search", source="server", disabled=True)
    finally:
        await session.close()

    refresh_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_toggle_tool_does_not_rediscover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vibe.app_server._resources.persist_mcp_toggle", AsyncMock())
    server = MCPStdio(name="search", transport="stdio", command="fake-cmd")
    config = build_test_vibe_config(mcp_servers=[server])
    agent_loop = build_test_agent_loop(config=config)
    refresh_mock = AsyncMock()
    agent_loop.tool_manager.refresh_remote_tools_async = refresh_mock
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        await session.resources.mcp.toggle(
            "search", source="server", disabled=True, tool_name="search_web"
        )
    finally:
        await session.close()

    refresh_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_mcp_login_rediscovers_tools() -> None:
    registry = LoginMCPRegistry()
    config = build_test_vibe_config(
        mcp_servers=[
            MCPHttp(
                name="search",
                transport="http",
                url="https://mcp.example.com",
                auth=MCPOAuth(type="oauth", scopes=[]),
            )
        ]
    )
    agent_loop = build_test_agent_loop(config=config, mcp_registry=registry)
    refresh_mock = AsyncMock()
    agent_loop.tool_manager.refresh_remote_tools_async = refresh_mock
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        async for _event in session.resources.mcp.login("search"):
            pass
    finally:
        await session.close()

    refresh_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_mcp_toggle_enable_broken_server_stays_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vibe.app_server._resources.persist_mcp_toggle", AsyncMock())
    server = MCPStdio(name="search", transport="stdio", command="fake-cmd")
    config = build_test_vibe_config(mcp_servers=[server])
    registry = FailingMCPRegistry(failing_server="search")
    agent_loop = build_test_agent_loop(config=config, mcp_registry=registry)
    session = await create_test_app_server_session(agent_loop)

    try:
        await session.connect()
        mcp_state = await session.resources.mcp.toggle(
            "search", source="server", disabled=False
        )
    finally:
        await session.close()

    broken = next(s for s in mcp_state.sources if s.name == "search")
    assert broken.status == MCPSourceStatus.UNAVAILABLE
