from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import AsyncMock, patch

from acp.schema import TextContentBlock, ToolCallProgress, ToolCallStart
import pytest

from tests.conftest import OrchestratorLoader, build_test_vibe_config, make_test_models
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.acp.exceptions import CompactionError
from vibe.core.agent_loop import AgentLoop, CompactionFailedError
from vibe.core.config import VibeConfigSchema
from vibe.core.session.session_id import shorten_session_id
from vibe.core.types import LLMMessage, Role


@pytest.fixture
def acp_agent_loop(
    backend: FakeBackend, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> VibeAcpAgentLoop:
    class PatchedAgent(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            kwargs["config_orchestrator"] = load_orchestrator(
                build_test_vibe_config(
                    models=make_test_models(auto_compact_threshold=1)
                )
            )
            super().__init__(*args, **kwargs, backend=backend)

    patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=PatchedAgent).start()
    vibe_acp_agent = VibeAcpAgentLoop()
    client = FakeClient()
    vibe_acp_agent.on_connect(client)
    client.on_connect(vibe_acp_agent)
    return vibe_acp_agent


class TestCompactEventHandling:
    @pytest.mark.asyncio
    async def test_prompt_handles_compact_events(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        """Verify prompt() sends tool_call session updates for compact events."""
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session = acp_agent_loop.sessions[session_response.session_id]
        session.agent_loop.stats.context_tokens = 2

        await acp_agent_loop.prompt(
            prompt=[TextContentBlock(type="text", text="Hello")],
            session_id=session_response.session_id,
        )

        mock_client = cast(FakeClient, acp_agent_loop.client)
        updates = [n.update for n in mock_client._session_updates]

        compact_start = next(
            (
                u
                for u in updates
                if isinstance(u, ToolCallStart)
                and u.title.startswith("Compacting conversation history")
            ),
            None,
        )
        assert compact_start is not None
        assert compact_start.session_update == "tool_call"
        assert compact_start.kind == "other"
        assert compact_start.status == "in_progress"

        compact_end = next(
            (
                u
                for u in updates
                if isinstance(u, ToolCallProgress)
                and u.tool_call_id == compact_start.tool_call_id
            ),
            None,
        )
        assert compact_end is not None
        assert compact_end.session_update == "tool_call_update"
        assert compact_end.status == "completed"

        assert compact_start.tool_call_id == compact_end.tool_call_id
        assert compact_end.content is not None
        compact_end_text = compact_end.content[0].content
        assert isinstance(compact_end_text, TextContentBlock)
        assert shorten_session_id(session_response.session_id) in compact_end_text.text
        assert (
            shorten_session_id(session.agent_loop.session_id) in compact_end_text.text
        )

    @pytest.mark.asyncio
    async def test_slash_compact_maps_compaction_failure(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        """A /compact failure surfaces as a mapped CompactionError, not a raw
        core exception — same as the auto-compaction path.
        """
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session = acp_agent_loop.sessions[session_response.session_id]
        await session.agent_loop.wait_until_ready()
        # Need >1 message so /compact does not early-return.
        session.agent_loop.messages.append(LLMMessage(role=Role.user, content="hello"))

        with patch.object(
            session.agent_loop,
            "compact",
            AsyncMock(side_effect=CompactionFailedError("tool_call")),
        ):
            with pytest.raises(CompactionError) as exc_info:
                await acp_agent_loop.prompt(
                    prompt=[TextContentBlock(type="text", text="/compact")],
                    session_id=session_response.session_id,
                )

        assert exc_info.value.code == -31006
        assert exc_info.value.data == {"reason": "tool_call"}
