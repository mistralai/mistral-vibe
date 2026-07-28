from __future__ import annotations

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.app_server import create_test_app_server_session
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.app_server.protocol import (
    AppServerResponseError,
    Notification,
    ProtocolErrorCode,
)
from vibe.core.config import MCPHttp, MCPOAuth
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
