from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from acp import AgentSideConnection, NewSessionRequest, SetSessionModelRequest
import pytest

from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_connection import FakeAgentSideConnection
from vibe.acp.acp_agent import VibeAcpAgent
from vibe.core.agent import Agent
from vibe.core.config import ModelConfig, VibeConfig
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
    config = VibeConfig(
        active_model="devstral-latest",
        models=[
            ModelConfig(
                name="devstral-latest", provider="mistral", alias="devstral-latest"
            ),
            ModelConfig(
                name="devstral-small", provider="mistral", alias="devstral-small"
            ),
        ],
    )

    class PatchedAgent(Agent):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **{**kwargs, "backend": backend})
            self.config = config

    patch("vibe.acp.acp_agent.VibeAgent", side_effect=PatchedAgent).start()

    vibe_acp_agent: VibeAcpAgent | None = None

    def _create_agent(connection: AgentSideConnection) -> VibeAcpAgent:
        nonlocal vibe_acp_agent
        vibe_acp_agent = VibeAcpAgent(connection)
        return vibe_acp_agent

    FakeAgentSideConnection(_create_agent)
    return vibe_acp_agent  # pyright: ignore[reportReturnType]


class TestACPNewSession:
    @pytest.mark.asyncio
    async def test_new_session_response_structure(
        self, acp_agent: VibeAcpAgent
    ) -> None:
        session_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )

        assert session_response.sessionId is not None
        acp_session = next(
            (
                s
                for s in acp_agent.sessions.values()
                if s.id == session_response.sessionId
            ),
            None,
        )
        assert acp_session is not None
        assert (
            acp_session.agent.interaction_logger.session_id
            == session_response.sessionId
        )

        assert session_response.sessionId == acp_session.agent.session_id

        assert session_response.models is not None
        assert session_response.models.currentModelId is not None
        assert session_response.models.availableModels is not None
        assert len(session_response.models.availableModels) == 2

        assert session_response.models.currentModelId == "devstral-latest"
        assert session_response.models.availableModels[0].modelId == "devstral-latest"
        assert session_response.models.availableModels[0].name == "devstral-latest"
        assert session_response.models.availableModels[1].modelId == "devstral-small"
        assert session_response.models.availableModels[1].name == "devstral-small"

        assert session_response.modes is not None
        assert session_response.modes.currentModeId is not None
        assert session_response.modes.availableModes is not None
        assert len(session_response.modes.availableModes) == 4

        assert session_response.modes.currentModeId == AgentMode.DEFAULT.value
        assert session_response.modes.availableModes[0].id == AgentMode.DEFAULT.value
        assert session_response.modes.availableModes[0].name == "Default"
        assert (
            session_response.modes.availableModes[1].id == AgentMode.AUTO_APPROVE.value
        )
        assert session_response.modes.availableModes[1].name == "Auto Approve"
        assert session_response.modes.availableModes[2].id == AgentMode.PLAN.value
        assert session_response.modes.availableModes[2].name == "Plan"
        assert (
            session_response.modes.availableModes[3].id == AgentMode.ACCEPT_EDITS.value
        )
        assert session_response.modes.availableModes[3].name == "Accept Edits"

    @pytest.mark.skip(reason="TODO: Fix this test")
    @pytest.mark.asyncio
    async def test_new_session_preserves_model_after_set_model(
        self, acp_agent: VibeAcpAgent
    ) -> None:
        session_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )
        session_id = session_response.sessionId

        assert session_response.models is not None
        assert session_response.models.currentModelId == "devstral-latest"

        response = await acp_agent.setSessionModel(
            SetSessionModelRequest(sessionId=session_id, modelId="devstral-small")
        )
        assert response is not None

        session_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )

        assert session_response.models is not None
        assert session_response.models.currentModelId == "devstral-small"
