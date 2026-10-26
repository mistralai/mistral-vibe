from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from acp.schema import AgentMessageChunk
import pytest

from tests.stubs.fake_client import FakeClient
from vibe.acp.agent import VibeAcpAgent
from vibe.acp.session import AcpSession
from vibe.app_server.events import ServerWarning
from vibe.app_server.models import (
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
    PublicError,
)
from vibe.app_server.protocol import (
    INTEGRATIONS_UNAVAILABLE_WARNING,
    ServerWarningParams,
)


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


def _session_for_events() -> AcpSession:
    return cast(
        AcpSession,
        SimpleNamespace(
            id="session-id",
            app_server=SimpleNamespace(
                resources=SimpleNamespace(runtime=SimpleNamespace(mcp=_public_mcp()))
            ),
        ),
    )


@pytest.mark.asyncio
async def test_acp_shows_the_integrations_that_could_not_load() -> None:
    agent = VibeAcpAgent()
    client = FakeClient()
    agent.on_connect(client)
    warning = "MCP server 'fail-http' failed to connect: down."

    await agent._forward_event(
        _session_for_events(),
        ServerWarning(
            ServerWarningParams(
                warning=PublicError(
                    message=warning, code=INTEGRATIONS_UNAVAILABLE_WARNING
                )
            )
        ),
    )

    messages = [
        notification.update
        for notification in client._session_updates
        if isinstance(notification.update, AgentMessageChunk)
    ]
    assert [message.content.text for message in messages] == [warning]


@pytest.mark.asyncio
async def test_acp_does_not_show_other_warnings_as_messages() -> None:
    agent = VibeAcpAgent()
    client = FakeClient()
    agent.on_connect(client)

    await agent._forward_event(
        _session_for_events(),
        ServerWarning(
            ServerWarningParams(warning=PublicError(message="hm", code="warning"))
        ),
    )

    assert not any(
        isinstance(notification.update, AgentMessageChunk)
        for notification in client._session_updates
    )
