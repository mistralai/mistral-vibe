from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from acp.schema import AgentMessageChunk, TextContentBlock
import pytest

from tests.acp.conftest import _create_acp_agent
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.core.agent_loop import AgentLoop
from vibe.core.types import LLMChunk, LLMMessage, LLMUsage, Role


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except ValueError:
        return False


def _make_response_chunk(content: str = "Hi") -> LLMChunk:
    return LLMChunk(
        message=LLMMessage(role=Role.assistant, content=content),
        usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
    )


@pytest.fixture
def two_turn_acp_agent_loop() -> VibeAcpAgentLoop:
    backend = FakeBackend([
        [_make_response_chunk("Hi")],
        [_make_response_chunk("Hi again")],
    ])

    class PatchedAgentLoop(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **{**kwargs, "backend": backend})

    patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=PatchedAgentLoop).start()
    return _create_acp_agent()


class TestPromptResponseUserMessageId:
    @pytest.mark.asyncio
    async def test_generates_user_message_id_when_client_provides_none(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )

        response = await acp_agent_loop.prompt(
            session_id=session_response.session_id,
            prompt=[TextContentBlock(type="text", text="hi")],
        )

        assert response.user_message_id is not None
        assert _is_uuid(response.user_message_id)

    @pytest.mark.asyncio
    async def test_echoes_client_provided_message_id(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        client_message_id = "550e8400-e29b-41d4-a716-446655440000"

        response = await acp_agent_loop.prompt(
            session_id=session_response.session_id,
            prompt=[TextContentBlock(type="text", text="hi")],
            message_id=client_message_id,
        )

        assert response.user_message_id == client_message_id

    @pytest.mark.asyncio
    async def test_user_message_ids_are_unique_across_turns(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        response_1 = await acp_agent_loop.prompt(
            session_id=session_id, prompt=[TextContentBlock(type="text", text="hi")]
        )
        response_2 = await acp_agent_loop.prompt(
            session_id=session_id,
            prompt=[TextContentBlock(type="text", text="hi again")],
        )

        assert response_1.user_message_id != response_2.user_message_id


class TestAgentMessageChunkMessageId:
    @pytest.mark.asyncio
    async def test_agent_message_chunk_has_message_id(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )

        await acp_agent_loop.prompt(
            session_id=session_response.session_id,
            prompt=[TextContentBlock(type="text", text="hi")],
        )

        fake_client: FakeClient = acp_agent_loop.client  # type: ignore
        agent_chunks = [
            u
            for u in fake_client._session_updates
            if isinstance(u.update, AgentMessageChunk)
        ]

        assert len(agent_chunks) >= 1
        assert agent_chunks[0].update.message_id is not None

    @pytest.mark.asyncio
    async def test_agent_message_ids_are_unique_across_turns(
        self, two_turn_acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await two_turn_acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id
        fake_client: FakeClient = two_turn_acp_agent_loop.client  # type: ignore

        await two_turn_acp_agent_loop.prompt(
            session_id=session_id, prompt=[TextContentBlock(type="text", text="hi")]
        )
        chunks_turn_1 = [
            u
            for u in fake_client._session_updates
            if isinstance(u.update, AgentMessageChunk)
        ]
        fake_client._session_updates.clear()

        await two_turn_acp_agent_loop.prompt(
            session_id=session_id,
            prompt=[TextContentBlock(type="text", text="hi again")],
        )
        chunks_turn_2 = [
            u
            for u in fake_client._session_updates
            if isinstance(u.update, AgentMessageChunk)
        ]

        assert chunks_turn_1[0].update.message_id != chunks_turn_2[0].update.message_id
