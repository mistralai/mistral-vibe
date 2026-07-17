from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from acp.schema import AgentMessageChunk
import pytest

from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.acp.session import AcpSessionLoop
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.messages import UserCommandMessage
from vibe.core.config import MCPOAuth, MCPStreamableHttp
from vibe.core.tools.mcp import MCPRegistry


def _registry_with_uncached_oauth(alias: str, *, disabled: bool = False) -> MCPRegistry:
    registry = MCPRegistry()
    registry.sync_active_servers([
        MCPStreamableHttp(
            name=alias,
            transport="streamable-http",
            url="https://mcp.example.com/mcp",
            auth=MCPOAuth(type="oauth", scopes=["read"]),
            disabled=disabled,
        )
    ])
    assert registry.needs_auth == set()
    return registry


@pytest.mark.asyncio
async def test_tui_mcp_auth_notice_uses_status_for_uncached_oauth() -> None:
    mount = AsyncMock()
    app = cast(
        VibeApp,
        SimpleNamespace(
            agent_loop=SimpleNamespace(
                mcp_registry=_registry_with_uncached_oauth("sentry")
            ),
            _mount_and_scroll=mount,
        ),
    )

    await VibeApp._show_mcp_auth_required_notice(app)

    mount.assert_awaited_once()
    args = mount.await_args
    assert args is not None
    message = args.args[0]
    assert isinstance(message, UserCommandMessage)
    assert "sentry" in message._content


@pytest.mark.asyncio
async def test_tui_mcp_auth_notice_skips_disabled_servers() -> None:
    mount = AsyncMock()
    app = cast(
        VibeApp,
        SimpleNamespace(
            agent_loop=SimpleNamespace(
                mcp_registry=_registry_with_uncached_oauth("sentry", disabled=True)
            ),
            _mount_and_scroll=mount,
        ),
    )

    await VibeApp._show_mcp_auth_required_notice(app)

    mount.assert_not_awaited()


@pytest.mark.asyncio
async def test_acp_mcp_auth_notice_skips_disabled_servers() -> None:
    agent = VibeAcpAgentLoop()
    client = FakeClient()
    agent.on_connect(client)
    session = cast(
        AcpSessionLoop,
        SimpleNamespace(
            id="session-id",
            agent_loop=SimpleNamespace(
                mcp_registry=_registry_with_uncached_oauth("sentry", disabled=True)
            ),
        ),
    )

    await agent._notify_mcp_auth_required(session)

    messages = [
        notification.update
        for notification in client._session_updates
        if isinstance(notification.update, AgentMessageChunk)
    ]
    assert messages == []


@pytest.mark.asyncio
async def test_acp_mcp_auth_notice_uses_status_for_uncached_oauth() -> None:
    agent = VibeAcpAgentLoop()
    client = FakeClient()
    agent.on_connect(client)
    session = cast(
        AcpSessionLoop,
        SimpleNamespace(
            id="session-id",
            agent_loop=SimpleNamespace(
                mcp_registry=_registry_with_uncached_oauth("sentry")
            ),
        ),
    )

    await agent._notify_mcp_auth_required(session)

    messages = [
        notification.update
        for notification in client._session_updates
        if isinstance(notification.update, AgentMessageChunk)
    ]
    assert len(messages) == 1
    assert "sentry" in messages[0].content.text


@pytest.mark.asyncio
async def test_acp_mcp_discovery_failures_surfaces_errors() -> None:
    agent = VibeAcpAgentLoop()
    client = FakeClient()
    agent.on_connect(client)
    session = cast(
        AcpSessionLoop,
        SimpleNamespace(
            id="session-id",
            agent_loop=SimpleNamespace(
                tool_manager=SimpleNamespace(
                    pop_mcp_errors=lambda: {"fail-http": "down", "broken": "no binary"}
                )
            ),
        ),
    )

    await agent._notify_mcp_discovery_failures(session)

    messages = [
        notification.update
        for notification in client._session_updates
        if isinstance(notification.update, AgentMessageChunk)
    ]
    assert len(messages) == 1
    text = messages[0].content.text
    assert "fail-http" in text and "down" in text
    assert "broken" in text and "no binary" in text


@pytest.mark.asyncio
async def test_acp_mcp_discovery_failures_no_message_when_empty() -> None:
    agent = VibeAcpAgentLoop()
    client = FakeClient()
    agent.on_connect(client)
    session = cast(
        AcpSessionLoop,
        SimpleNamespace(
            id="session-id",
            agent_loop=SimpleNamespace(
                tool_manager=SimpleNamespace(pop_mcp_errors=lambda: {})
            ),
        ),
    )

    await agent._notify_mcp_discovery_failures(session)

    messages = [
        notification.update
        for notification in client._session_updates
        if isinstance(notification.update, AgentMessageChunk)
    ]
    assert messages == []
