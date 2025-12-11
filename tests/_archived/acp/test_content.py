from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from acp import AgentSideConnection, NewSessionRequest, PromptRequest
from acp.schema import (
    EmbeddedResourceContentBlock,
    ResourceContentBlock,
    TextContentBlock,
    TextResourceContents,
)
import pytest

from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_connection import FakeAgentSideConnection
from vibe.acp.acp_agent import VibeAcpAgent
from vibe.core.agent import Agent
from vibe.core.types import LLMChunk, LLMMessage, LLMUsage, Role


@pytest.fixture
def backend() -> FakeBackend:
    backend = FakeBackend(
        results=[
            LLMChunk(
                message=LLMMessage(role=Role.assistant, content="Hi"),
                finish_reason="end_turn",
                usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
            )
        ]
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


class TestACPContent:
    @pytest.mark.asyncio
    async def test_text_content(
        self, acp_agent: VibeAcpAgent, backend: FakeBackend
    ) -> None:
        session_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )
        prompt_request = PromptRequest(
            prompt=[TextContentBlock(type="text", text="Say hi")],
            sessionId=session_response.sessionId,
        )

        response = await acp_agent.prompt(params=prompt_request)

        assert response.stopReason == "end_turn"
        user_message = next(
            (msg for msg in backend._requests_messages[0] if msg.role == Role.user),
            None,
        )
        assert user_message is not None, "User message not found in backend requests"
        assert user_message.content == "Say hi"

    @pytest.mark.asyncio
    async def test_resource_content(
        self, acp_agent: VibeAcpAgent, backend: FakeBackend
    ) -> None:
        session_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )
        prompt_request = PromptRequest(
            prompt=[
                TextContentBlock(type="text", text="What does this file do?"),
                EmbeddedResourceContentBlock(
                    type="resource",
                    resource=TextResourceContents(
                        uri="file:///home/my_file.py",
                        text="def hello():\n    print('Hello, world!')",
                        mimeType="text/x-python",
                    ),
                ),
            ],
            sessionId=session_response.sessionId,
        )

        response = await acp_agent.prompt(params=prompt_request)

        assert response.stopReason == "end_turn"
        user_message = next(
            (msg for msg in backend._requests_messages[0] if msg.role == Role.user),
            None,
        )
        assert user_message is not None, "User message not found in backend requests"
        expected_content = (
            "What does this file do?"
            + "\n\npath: file:///home/my_file.py"
            + "\ncontent: def hello():\n    print('Hello, world!')"
        )
        assert user_message.content == expected_content

    @pytest.mark.asyncio
    async def test_resource_link_content(
        self, acp_agent: VibeAcpAgent, backend: FakeBackend
    ) -> None:
        session_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )
        prompt_request = PromptRequest(
            prompt=[
                TextContentBlock(type="text", text="Analyze this resource"),
                ResourceContentBlock(
                    type="resource_link",
                    uri="file:///home/document.pdf",
                    name="document.pdf",
                    title="Important Document",
                    description="A PDF document containing project specifications",
                    mimeType="application/pdf",
                    size=1024,
                ),
            ],
            sessionId=session_response.sessionId,
        )

        response = await acp_agent.prompt(params=prompt_request)

        assert response.stopReason == "end_turn"
        user_message = next(
            (msg for msg in backend._requests_messages[0] if msg.role == Role.user),
            None,
        )
        assert user_message is not None, "User message not found in backend requests"
        expected_content = (
            "Analyze this resource"
            + "\n\nuri: file:///home/document.pdf"
            + "\nname: document.pdf"
            + "\ntitle: Important Document"
            + "\ndescription: A PDF document containing project specifications"
            + "\nmimeType: application/pdf"
            + "\nsize: 1024"
        )
        assert user_message.content == expected_content

    @pytest.mark.asyncio
    async def test_resource_link_minimal(
        self, acp_agent: VibeAcpAgent, backend: FakeBackend
    ) -> None:
        session_response = await acp_agent.newSession(
            NewSessionRequest(cwd=str(Path.cwd()), mcpServers=[])
        )
        prompt_request = PromptRequest(
            prompt=[
                ResourceContentBlock(
                    type="resource_link",
                    uri="file:///home/minimal.txt",
                    name="minimal.txt",
                )
            ],
            sessionId=session_response.sessionId,
        )

        response = await acp_agent.prompt(params=prompt_request)

        assert response.stopReason == "end_turn"
        user_message = next(
            (msg for msg in backend._requests_messages[0] if msg.role == Role.user),
            None,
        )
        assert user_message is not None, "User message not found in backend requests"
        expected_content = "uri: file:///home/minimal.txt\nname: minimal.txt"
        assert user_message.content == expected_content
