from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.acp.agent import VibeAcpAgent
from vibe.acp.session import AcpSession
from vibe.app_server.session import AppServerSession


def _make_agent(*, connection_closed: bool) -> tuple[VibeAcpAgent, AcpSession]:
    agent = VibeAcpAgent()
    client = MagicMock()
    connection = MagicMock()
    connection._closed = connection_closed
    connection._disconnected = connection_closed
    client._conn = connection
    agent.client = cast(Any, client)

    app_server = MagicMock(spec=AppServerSession)
    app_server.act = MagicMock(side_effect=asyncio.CancelledError)
    session = AcpSession(
        session_id="test-session",
        app_server=app_server,
        cwd=Path.cwd(),
        commands=MagicMock(),
    )
    agent.sessions[session.id] = session
    command_controller = cast(Any, agent)._command_controller
    command_controller.execute = AsyncMock(return_value=None)
    cast(Any, agent)._record_skill_command = MagicMock()
    cast(Any, agent)._prepare_turn_input = AsyncMock(return_value=MagicMock())
    cast(Any, agent)._send_usage_update = MagicMock()
    cast(Any, agent)._usage = MagicMock(return_value=None)
    return agent, session


class TestPromptCancel:
    @pytest.mark.asyncio
    async def test_connection_teardown_reraises_without_usage_update(self) -> None:
        agent, session = _make_agent(connection_closed=True)

        with pytest.raises(asyncio.CancelledError):
            await agent.prompt(session.id, [])

        send_usage_update = cast(Any, agent)._send_usage_update
        send_usage_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_turn_level_cancel_returns_cancelled_and_sends_usage(self) -> None:
        agent, session = _make_agent(connection_closed=False)

        response = await agent.prompt(session.id, [])

        assert response.stop_reason == "cancelled"
        send_usage_update = cast(Any, agent)._send_usage_update
        send_usage_update.assert_called_once_with(session)
