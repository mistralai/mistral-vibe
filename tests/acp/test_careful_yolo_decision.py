from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any, cast

from acp.schema import ContentToolCallContent, TextContentBlock, ToolCallProgress
import pytest

from tests.acp.conftest import _create_acp_agent
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.acp.session import AcpSessionLoop
from vibe.core.types import BaseEvent, SmartAutoDecisionEvent


class _DecisionAgentLoop:
    def act(self, *_args: Any, **_kwargs: Any) -> AsyncGenerator[BaseEvent, None]:
        async def events() -> AsyncGenerator[BaseEvent, None]:
            yield SmartAutoDecisionEvent(
                tool_name="bash",
                tool_call_id="call-1",
                verdict="ASK",
                reason="This creates a commit without an explicit request.",
            )

        return events()


@pytest.mark.asyncio
async def test_careful_yolo_decision_is_exposed_as_tool_progress() -> None:
    acp_loop: VibeAcpAgentLoop = _create_acp_agent()
    session = cast(
        AcpSessionLoop, SimpleNamespace(id="session-1", agent_loop=_DecisionAgentLoop())
    )

    updates = [
        update async for update in acp_loop._run_agent_loop(session, "make a change")
    ]

    assert len(updates) == 1
    progress = updates[0]
    assert isinstance(progress, ToolCallProgress)
    assert progress.tool_call_id == "call-1"
    assert progress.field_meta == {"tool_name": "bash", "careful_yolo_verdict": "ASK"}
    assert progress.content is not None
    content = progress.content[0]
    assert isinstance(content, ContentToolCallContent)
    assert isinstance(content.content, TextContentBlock)
    assert content.content.text == (
        "Careful YOLO: ASK — This creates a commit without an explicit request."
    )
