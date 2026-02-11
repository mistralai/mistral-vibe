from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from acp.schema import AgentThoughtChunk, TextContentBlock
import pytest

from tests.acp.conftest import _create_acp_agent
from tests.conftest import build_test_vibe_config
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.core.agent_loop import AgentLoop
from vibe.core.types import LLMChunk, LLMMessage, LLMUsage, Role


def _create_backend_with_reasoning(
    reasoning_content: str, content: str = "Hi"
) -> FakeBackend:
    return FakeBackend(
        LLMChunk(
            message=LLMMessage(
                role=Role.assistant,
                content=content,
                reasoning_content=reasoning_content,
            ),
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
        )
    )


@pytest.fixture
def backend_with_reasoning() -> FakeBackend:
    return _create_backend_with_reasoning("Let me think about this...")


@pytest.fixture
def acp_agent_loop_with_reasoning(
    backend_with_reasoning: FakeBackend,
) -> VibeAcpAgentLoop:
    config = build_test_vibe_config(active_model="devstral-latest")

    class PatchedAgentLoop(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **{**kwargs, "backend": backend_with_reasoning})
            self._base_config = config
            self.agent_manager.invalidate_config()

    patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=PatchedAgentLoop).start()
    return _create_acp_agent()


class TestACPAgentThought:
    @pytest.mark.asyncio
    async def test_prompt_with_reasoning_emits_agent_thought_chunk(
        self, acp_agent_loop_with_reasoning: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop_with_reasoning.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        await acp_agent_loop_with_reasoning.prompt(
            session_id=session_id,
            prompt=[TextContentBlock(type="text", text="Just say hi")],
        )

        fake_client: FakeClient = acp_agent_loop_with_reasoning.client  # type: ignore
        thought_updates = [
            update
            for update in fake_client._session_updates
            if isinstance(update.update, AgentThoughtChunk)
        ]

        assert len(thought_updates) == 1
        thought_chunk = thought_updates[0].update
        assert thought_chunk.session_update == "agent_thought_chunk"
        assert thought_chunk.content is not None
        assert isinstance(thought_chunk.content, TextContentBlock)
        assert thought_chunk.content.text == "Let me think about this..."

    @pytest.mark.asyncio
    async def test_prompt_without_reasoning_does_not_emit_agent_thought_chunk(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        await acp_agent_loop.prompt(
            session_id=session_id,
            prompt=[TextContentBlock(type="text", text="Just say hi")],
        )

        fake_client: FakeClient = acp_agent_loop.client  # type: ignore
        thought_updates = [
            update
            for update in fake_client._session_updates
            if isinstance(update.update, AgentThoughtChunk)
        ]

        assert len(thought_updates) == 0

    @pytest.mark.asyncio
    async def test_agent_thought_chunk_contains_text_content_block(
        self, acp_agent_loop_with_reasoning: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop_with_reasoning.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        await acp_agent_loop_with_reasoning.prompt(
            session_id=session_id, prompt=[TextContentBlock(type="text", text="Hello")]
        )

        fake_client: FakeClient = acp_agent_loop_with_reasoning.client  # type: ignore
        thought_updates = [
            update
            for update in fake_client._session_updates
            if isinstance(update.update, AgentThoughtChunk)
        ]

        assert len(thought_updates) == 1
        thought_chunk = thought_updates[0].update
        assert thought_chunk.content.type == "text"

    @pytest.mark.asyncio
    async def test_agent_thought_chunk_contains_message_id(
        self, acp_agent_loop_with_reasoning: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop_with_reasoning.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id

        await acp_agent_loop_with_reasoning.prompt(
            session_id=session_id, prompt=[TextContentBlock(type="text", text="Hello")]
        )

        fake_client: FakeClient = acp_agent_loop_with_reasoning.client  # type: ignore
        thought_updates = [
            update
            for update in fake_client._session_updates
            if isinstance(update.update, AgentThoughtChunk)
        ]

        assert len(thought_updates) == 1
        thought_chunk = thought_updates[0].update
        assert thought_chunk.field_meta is not None
        assert "messageId" in thought_chunk.field_meta
        assert thought_chunk.field_meta["messageId"] is not None
