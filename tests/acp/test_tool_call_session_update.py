from __future__ import annotations

from acp.schema import ToolCallStart

from vibe.acp.tools.builtins.read import Read
from vibe.acp.tools.session_update import tool_call_session_update
from vibe.core.tools.builtins.read import ReadArgs
from vibe.core.types import ToolCallEvent


class TestToolCallSessionUpdate:
    def _create_event(self) -> ToolCallEvent:
        return ToolCallEvent(
            tool_name="read",
            tool_call_id="test_call_123",
            args=ReadArgs(file_path="/tmp/test.txt"),
            tool_class=Read,
        )

    def test_returns_tool_call_start(self) -> None:
        event = self._create_event()

        update = tool_call_session_update(event)

        assert update is not None
        assert isinstance(update, ToolCallStart)
        assert update.session_update == "tool_call"
        assert update.tool_call_id == "test_call_123"

    def test_returns_tool_call_start_for_streaming_event(self) -> None:
        event = ToolCallEvent(
            tool_name="read", tool_call_id="test_call_123", tool_class=Read, args=None
        )

        update = tool_call_session_update(event)

        assert update is not None
        assert isinstance(update, ToolCallStart)
        assert update.session_update == "tool_call"
        assert update.tool_call_id == "test_call_123"
        assert update.kind == "read"
        assert update.raw_input is None
