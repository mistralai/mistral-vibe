from __future__ import annotations

from pathlib import Path
from typing import cast
from unittest.mock import patch

from acp.schema import TextContentBlock, ToolCallProgress, ToolCallStart
import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.core.agent_loop import AgentLoop


@pytest.fixture
def acp_agent_loop(backend: FakeBackend) -> VibeAcpAgentLoop:
    class PatchedAgent(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            # Force our config with auto_compact_threshold=1
            kwargs["config"] = build_test_vibe_config(auto_compact_threshold=1)
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
