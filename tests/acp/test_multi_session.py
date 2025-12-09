from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from acp import (
    PROTOCOL_VERSION,
    InitializeRequest,
    NewSessionRequest,
    PromptRequest,
    RequestError,
)
from acp.schema import TextContentBlock
import pytest
from pytest import raises

from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_connection import FakeAgentSideConnection
from vibe.acp.acp_agent import VibeAcpAgent
from vibe.core.agent import Agent
from vibe.core.config import ModelConfig, VibeConfig
from vibe.core.types import Role


@pytest.fixture
def backend() -> FakeBackend:
    backend = FakeBackend()
    return backend


@pytest.fixture
def acp_agent(backend: FakeBackend) -> VibeAcpAgent:
    config = VibeConfig(
        active_model="devstral-latest",
        models=[
            ModelConfig(
                name="devstral-latest", provider="mistral", alias="devstral-latest"
            )
        ],
    )

    class PatchedAgent(Agent):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.backend = backend
            self.config = config

    patch("vibe.acp.acp_agent.VibeAgent", side_effect=PatchedAgent).start()

    vibe_acp_agent: VibeAcpAgent | None = None

    def _create_agent(connection: Any) -> VibeAcpAgent:
        nonlocal vibe_acp_agent
        vibe_acp_agent = VibeAcpAgent(connection)
        return vibe_acp_agent

    FakeAgentSideConnection(_create_agent)
    return vibe_acp_agent  # pyright: ignore[reportReturnType]


class TestMultiSessionCore:
    @pytest.mark.asyncio
    async def test_different_sessions_use_different_agents(
        self, acp_agent: VibeAcpAgent
    ) -> None:
        await acp_agent.initialize(InitializeRequest(protocolVersion=PROTOCOL_VERSION))
        session1_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )
        session1 = acp_agent.sessions[session1_response.sessionId]
        session2_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )
        session2 = acp_agent.sessions[session2_response.sessionId]

        assert session1.id != session2.id
        # Each agent should be independent
        assert session1.agent is not session2.agent
        assert id(session1.agent) != id(session2.agent)

    @pytest.mark.asyncio
    async def test_error_on_nonexistent_session(self, acp_agent: VibeAcpAgent) -> None:
        await acp_agent.initialize(InitializeRequest(protocolVersion=PROTOCOL_VERSION))
        await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )

        fake_session_id = "fake-session-id-" + str(uuid4())

        with raises(RequestError) as exc_info:
            await acp_agent.prompt(
                PromptRequest(
                    sessionId=fake_session_id,
                    prompt=[TextContentBlock(type="text", text="Hello, world!")],
                )
            )

        assert isinstance(exc_info.value, RequestError)
        assert str(exc_info.value) == "Invalid params"

    @pytest.mark.asyncio
    async def test_simultaneous_message_processing(
        self, acp_agent: VibeAcpAgent, backend: FakeBackend
    ) -> None:
        await acp_agent.initialize(InitializeRequest(protocolVersion=PROTOCOL_VERSION))
        session1_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )
        session1 = acp_agent.sessions[session1_response.sessionId]
        session2_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )
        session2 = acp_agent.sessions[session2_response.sessionId]

        backend._chunks = [
            mock_llm_chunk(content="Response 1", finish_reason="stop"),
            mock_llm_chunk(content="Response 2", finish_reason="stop"),
        ]

        async def run_session1():
            await acp_agent.prompt(
                PromptRequest(
                    sessionId=session1.id,
                    prompt=[TextContentBlock(type="text", text="Prompt for session 1")],
                )
            )

        async def run_session2():
            await acp_agent.prompt(
                PromptRequest(
                    sessionId=session2.id,
                    prompt=[TextContentBlock(type="text", text="Prompt for session 2")],
                )
            )

        await asyncio.gather(run_session1(), run_session2())

        user_message1 = next(
            (msg for msg in session1.agent.messages if msg.role == Role.user), None
        )
        assert user_message1 is not None
        assert user_message1.content == "Prompt for session 1"
        assistant_message1 = next(
            (msg for msg in session1.agent.messages if msg.role == Role.assistant), None
        )
        assert assistant_message1 is not None
        assert assistant_message1.content == "Response 1"
        user_message2 = next(
            (msg for msg in session2.agent.messages if msg.role == Role.user), None
        )
        assert user_message2 is not None
        assert user_message2.content == "Prompt for session 2"
        assistant_message2 = next(
            (msg for msg in session2.agent.messages if msg.role == Role.assistant), None
        )
        assert assistant_message2 is not None
        assert assistant_message2.content == "Response 2"
