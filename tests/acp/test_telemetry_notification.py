from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests.acp.conftest import _create_acp_agent
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.acp.exceptions import InvalidRequestError
from vibe.core.agent_loop import AgentLoop


@pytest.fixture
def acp_agent_loop(backend) -> VibeAcpAgentLoop:
    class PatchedAgentLoop(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **{**kwargs, "backend": backend})

    patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=PatchedAgentLoop).start()
    return _create_acp_agent()


class TestTelemetryNotification:
    @pytest.mark.asyncio
    async def test_ignores_unknown_event_gracefully(
        self,
        acp_agent_loop: VibeAcpAgentLoop,
        telemetry_events: list[dict[str, object]],
    ) -> None:
        session = await acp_agent_loop.new_session(cwd=str(Path.cwd()), mcp_servers=[])
        telemetry_events.clear()

        await acp_agent_loop.ext_notification(
            "telemetry/send",
            {
                "event": "vibe.unsupported_event",
                "session_id": session.session_id,
                "properties": {"context_type": "file"},
            },
        )

        assert telemetry_events == []

    @pytest.mark.asyncio
    async def test_raises_on_invalid_params(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        with pytest.raises(InvalidRequestError):
            await acp_agent_loop.ext_notification(
                "telemetry/send",
                {"event": "vibe.some_event"},  # missing session_id
            )
