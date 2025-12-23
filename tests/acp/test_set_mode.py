from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from acp import AgentSideConnection, NewSessionRequest, SetSessionModeRequest
import pytest

from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_connection import FakeAgentSideConnection
from vibe.acp.acp_agent import VibeAcpAgent
from vibe.core.agent import Agent
from vibe.core.modes import AgentMode
from vibe.core.types import LLMChunk, LLMMessage, LLMUsage, Role


@pytest.fixture
def backend() -> FakeBackend:
    backend = FakeBackend(
        LLMChunk(
            message=LLMMessage(role=Role.assistant, content="Hi"),
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
        )
    )
    return backend


@pytest.fixture
def acp_agent(backend: FakeBackend) -> VibeAcpAgent:
    class PatchedAgent(Agent):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs, backend=backend)

    patch("vibe.acp.acp_agent.VibeAgent", side_effect=PatchedAgent).start()

    vibe_acp_agent: VibeAcpAgent | None = None

    def _create_agent(connection: AgentSideConnection) -> VibeAcpAgent:
        nonlocal vibe_acp_agent
        vibe_acp_agent = VibeAcpAgent(connection)
        return vibe_acp_agent

    FakeAgentSideConnection(_create_agent)
    return vibe_acp_agent  # pyright: ignore[reportReturnType]


class TestACPSetMode:
    @pytest.mark.asyncio
    async def test_set_mode_to_default(self, acp_agent: VibeAcpAgent) -> None:
        session_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )
        session_id = session_response.sessionId
        acp_session = next(
            (s for s in acp_agent.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        await acp_session.agent.switch_mode(AgentMode.AUTO_APPROVE)

        response = await acp_agent.setSessionMode(
            SetSessionModeRequest(sessionId=session_id, modeId=AgentMode.DEFAULT.value)
        )

        assert response is not None
        assert acp_session.agent.mode == AgentMode.DEFAULT
        assert acp_session.agent.auto_approve is False

    @pytest.mark.asyncio
    async def test_set_mode_to_auto_approve(self, acp_agent: VibeAcpAgent) -> None:
        session_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )
        session_id = session_response.sessionId
        acp_session = next(
            (s for s in acp_agent.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        assert acp_session.agent.mode == AgentMode.DEFAULT
        assert acp_session.agent.auto_approve is False

        response = await acp_agent.setSessionMode(
            SetSessionModeRequest(
                sessionId=session_id, modeId=AgentMode.AUTO_APPROVE.value
            )
        )

        assert response is not None
        assert acp_session.agent.mode == AgentMode.AUTO_APPROVE
        assert acp_session.agent.auto_approve is True

    @pytest.mark.asyncio
    async def test_set_mode_to_plan(self, acp_agent: VibeAcpAgent) -> None:
        session_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )
        session_id = session_response.sessionId
        acp_session = next(
            (s for s in acp_agent.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        assert acp_session.agent.mode == AgentMode.DEFAULT

        response = await acp_agent.setSessionMode(
            SetSessionModeRequest(sessionId=session_id, modeId=AgentMode.PLAN.value)
        )

        assert response is not None
        assert acp_session.agent.mode == AgentMode.PLAN
        assert (
            acp_session.agent.auto_approve is True
        )  # Plan mode auto-approves read-only tools

    @pytest.mark.asyncio
    async def test_set_mode_to_accept_edits(self, acp_agent: VibeAcpAgent) -> None:
        session_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )
        session_id = session_response.sessionId
        acp_session = next(
            (s for s in acp_agent.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        assert acp_session.agent.mode == AgentMode.DEFAULT

        response = await acp_agent.setSessionMode(
            SetSessionModeRequest(
                sessionId=session_id, modeId=AgentMode.ACCEPT_EDITS.value
            )
        )

        assert response is not None
        assert acp_session.agent.mode == AgentMode.ACCEPT_EDITS
        assert (
            acp_session.agent.auto_approve is False
        )  # Accept Edits mode doesn't auto-approve all

    @pytest.mark.asyncio
    async def test_set_mode_invalid_mode_returns_none(
        self, acp_agent: VibeAcpAgent
    ) -> None:
        session_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )
        session_id = session_response.sessionId
        acp_session = next(
            (s for s in acp_agent.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        initial_mode = acp_session.agent.mode
        initial_auto_approve = acp_session.agent.auto_approve

        response = await acp_agent.setSessionMode(
            SetSessionModeRequest(sessionId=session_id, modeId="invalid-mode")
        )

        assert response is None
        assert acp_session.agent.mode == initial_mode
        assert acp_session.agent.auto_approve == initial_auto_approve

    @pytest.mark.asyncio
    async def test_set_mode_to_same_mode(self, acp_agent: VibeAcpAgent) -> None:
        session_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )
        session_id = session_response.sessionId
        acp_session = next(
            (s for s in acp_agent.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        initial_mode = AgentMode.DEFAULT
        assert acp_session.agent.mode == initial_mode

        response = await acp_agent.setSessionMode(
            SetSessionModeRequest(sessionId=session_id, modeId=initial_mode.value)
        )

        assert response is not None
        assert acp_session.agent.mode == initial_mode
        assert acp_session.agent.auto_approve is False

    @pytest.mark.asyncio
    async def test_set_mode_with_empty_string(self, acp_agent: VibeAcpAgent) -> None:
        session_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )
        session_id = session_response.sessionId
        acp_session = next(
            (s for s in acp_agent.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        initial_mode = acp_session.agent.mode
        initial_auto_approve = acp_session.agent.auto_approve

        response = await acp_agent.setSessionMode(
            SetSessionModeRequest(sessionId=session_id, modeId="")
        )

        assert response is None
        assert acp_session.agent.mode == initial_mode
        assert acp_session.agent.auto_approve == initial_auto_approve
