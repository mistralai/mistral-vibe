from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock

from acp import RequestError
from acp.schema import TextContentBlock
import pytest

from vibe.acp.acp_agent_loop import VibeAcpAgentLoop


class TestCloseSession:
    @pytest.mark.asyncio
    async def test_close_session_removes_session_and_closes_resources(
        self, acp_agent_loop: VibeAcpAgentLoop, telemetry_events: list[dict[str, Any]]
    ) -> None:
        session_response = await acp_agent_loop.new_session(cwd=".", mcp_servers=[])
        session = acp_agent_loop.sessions[session_response.session_id]

        backend_close = AsyncMock()
        telemetry_close = AsyncMock()
        cast(Any, session.agent_loop.backend).close = backend_close
        session.agent_loop.telemetry_client.aclose = telemetry_close

        response = await acp_agent_loop.close_session(session_response.session_id)

        assert response is not None
        assert session_response.session_id not in acp_agent_loop.sessions
        backend_close.assert_awaited_once()
        telemetry_close.assert_awaited_once()
        session_closed_events = [
            event
            for event in telemetry_events
            if event["event_name"] == "vibe.session_closed"
        ]
        assert len(session_closed_events) == 1
        assert (
            session_closed_events[0]["properties"]["session_id"]
            == session_response.session_id
        )
        assert session_closed_events[0]["properties"]["agent_entrypoint"] == "acp"

    @pytest.mark.asyncio
    async def test_close_session_cancels_active_prompt(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(cwd=".", mcp_servers=[])
        session = acp_agent_loop.sessions[session_response.session_id]

        async def wait_forever() -> None:
            await asyncio.Event().wait()

        task = session.set_prompt_task(wait_forever())
        session.agent_loop.telemetry_client.aclose = AsyncMock()

        await acp_agent_loop.close_session(session_response.session_id)

        assert task.cancelled()

    @pytest.mark.asyncio
    async def test_close_session_cancels_background_tasks(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(cwd=".", mcp_servers=[])
        session = acp_agent_loop.sessions[session_response.session_id]

        background_event = asyncio.Event()

        async def background_work() -> None:
            await background_event.wait()

        bg_task = session.spawn(background_work())
        assert bg_task is not None

        session.agent_loop.telemetry_client.aclose = AsyncMock()

        await acp_agent_loop.close_session(session_response.session_id)

        assert bg_task.cancelled()

    @pytest.mark.asyncio
    async def test_emit_session_closed_for_active_sessions(
        self, acp_agent_loop: VibeAcpAgentLoop, telemetry_events: list[dict[str, Any]]
    ) -> None:
        session1 = await acp_agent_loop.new_session(cwd=".", mcp_servers=[])
        session2 = await acp_agent_loop.new_session(cwd=".", mcp_servers=[])

        await acp_agent_loop.emit_session_closed_for_active_sessions()

        session_closed_events = [
            e for e in telemetry_events if e["event_name"] == "vibe.session_closed"
        ]
        emitted_ids = {e["properties"]["session_id"] for e in session_closed_events}
        assert len(session_closed_events) == 2
        assert session1.session_id in emitted_ids
        assert session2.session_id in emitted_ids

    @pytest.mark.asyncio
    async def test_close_session_rejects_new_background_tasks(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(cwd=".", mcp_servers=[])
        session = acp_agent_loop.sessions[session_response.session_id]
        session.agent_loop.telemetry_client.aclose = AsyncMock()

        await acp_agent_loop.close_session(session_response.session_id)

        async def noop() -> None:
            pass

        assert session.spawn(noop()) is None

    @pytest.mark.asyncio
    async def test_closed_session_rejects_new_prompts(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(cwd=".", mcp_servers=[])
        session = acp_agent_loop.sessions[session_response.session_id]
        session.agent_loop.telemetry_client.aclose = AsyncMock()

        await acp_agent_loop.close_session(session_response.session_id)

        with pytest.raises(RequestError, match="Session not found"):
            await acp_agent_loop.prompt(
                prompt=[TextContentBlock(type="text", text="Hello")],
                session_id=session_response.session_id,
            )
