from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

from acp.schema import AgentMessageChunk
import pytest

from tests.stubs.fake_client import FakeClient
from vibe.acp.agent import VibeAcpAgent
from vibe.acp.session import AcpSession
from vibe.app_server.models import (
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
)
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.messages import UserCommandMessage


def _public_mcp(
    alias: str = "", *, disabled: bool = False, errors: dict[str, str] | None = None
) -> MCPState:
    return MCPState(
        sources=(
            [
                MCPSourceSummary(
                    name=alias,
                    kind=MCPSourceKind.SERVER,
                    transport="streamable-http",
                    status=(
                        MCPSourceStatus.DISABLED
                        if disabled
                        else MCPSourceStatus.NEEDS_AUTH
                    ),
                )
            ]
            if alias
            else []
        ),
        discovery_errors=errors or {},
    )


@pytest.mark.asyncio
async def test_tui_mcp_auth_notice_uses_status_for_uncached_oauth() -> None:
    mount = AsyncMock()
    app = cast(
        VibeApp,
        SimpleNamespace(
            app_server=SimpleNamespace(
                resources=SimpleNamespace(
                    runtime=SimpleNamespace(mcp=_public_mcp("sentry"))
                )
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
            app_server=SimpleNamespace(
                resources=SimpleNamespace(
                    runtime=SimpleNamespace(mcp=_public_mcp("sentry", disabled=True))
                )
            ),
            _mount_and_scroll=mount,
        ),
    )

    await VibeApp._show_mcp_auth_required_notice(app)

    mount.assert_not_awaited()


def test_tui_mcp_discovery_failures_surface_errors() -> None:
    notify = Mock()
    app = cast(
        VibeApp,
        SimpleNamespace(
            app_server=SimpleNamespace(
                resources=SimpleNamespace(
                    runtime=SimpleNamespace(
                        mcp=_public_mcp(
                            errors={"fail-http": "down", "broken": "no binary"}
                        )
                    )
                )
            ),
            notify=notify,
        ),
    )

    VibeApp._show_mcp_discovery_failures(app)

    assert [call.args[0] for call in notify.call_args_list] == [
        "MCP server 'broken' failed to connect: no binary",
        "MCP server 'fail-http' failed to connect: down",
    ]


@pytest.mark.asyncio
async def test_acp_mcp_auth_notice_skips_disabled_servers() -> None:
    agent = VibeAcpAgent()
    client = FakeClient()
    agent.on_connect(client)
    session = cast(
        AcpSession,
        SimpleNamespace(
            id="session-id",
            app_server=SimpleNamespace(
                resources=SimpleNamespace(
                    runtime=SimpleNamespace(mcp=_public_mcp("sentry", disabled=True))
                )
            ),
        ),
    )

    await agent._notify_mcp_auth(session)

    messages = [
        notification.update
        for notification in client._session_updates
        if isinstance(notification.update, AgentMessageChunk)
    ]
    assert messages == []


@pytest.mark.asyncio
async def test_acp_mcp_auth_notice_uses_status_for_uncached_oauth() -> None:
    agent = VibeAcpAgent()
    client = FakeClient()
    agent.on_connect(client)
    session = cast(
        AcpSession,
        SimpleNamespace(
            id="session-id",
            app_server=SimpleNamespace(
                resources=SimpleNamespace(
                    runtime=SimpleNamespace(mcp=_public_mcp("sentry"))
                )
            ),
        ),
    )

    await agent._notify_mcp_auth(session)

    messages = [
        notification.update
        for notification in client._session_updates
        if isinstance(notification.update, AgentMessageChunk)
    ]
    assert len(messages) == 1
    assert "sentry" in messages[0].content.text


@pytest.mark.asyncio
async def test_acp_mcp_discovery_failures_surfaces_errors() -> None:
    agent = VibeAcpAgent()
    client = FakeClient()
    agent.on_connect(client)
    session = cast(
        AcpSession,
        SimpleNamespace(
            id="session-id",
            app_server=SimpleNamespace(
                resources=SimpleNamespace(
                    runtime=SimpleNamespace(
                        mcp=_public_mcp(
                            errors={"fail-http": "down", "broken": "no binary"}
                        )
                    )
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
    agent = VibeAcpAgent()
    client = FakeClient()
    agent.on_connect(client)
    session = cast(
        AcpSession,
        SimpleNamespace(
            id="session-id",
            app_server=SimpleNamespace(
                resources=SimpleNamespace(runtime=SimpleNamespace(mcp=_public_mcp()))
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
