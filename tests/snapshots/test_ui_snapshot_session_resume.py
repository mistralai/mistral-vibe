from __future__ import annotations

from textual.pilot import Pilot

from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from vibe.core.types import FunctionCall, LLMMessage, Role, ToolCall


class SnapshotTestAppWithResumedSession(BaseSnapshotTestApp):
    def __init__(self) -> None:
        super().__init__()
        # Simulate a previous session with messages
        user_msg = LLMMessage(role=Role.user, content="Hello, how are you?")
        assistant_msg = LLMMessage(
            role=Role.assistant,
            content="I'm doing well, thank you! Let me read that file for you.",
            tool_calls=[
                ToolCall(
                    id="tool_call_1",
                    index=0,
                    function=FunctionCall(
                        name="read_file", arguments='{"path": "test.txt"}'
                    ),
                )
            ],
        )
        tool_result_msg = LLMMessage(
            role=Role.tool,
            content="File content: This is a test file with some content.",
            name="read_file",
            tool_call_id="tool_call_1",
        )

        self.agent_loop.messages.extend([user_msg, assistant_msg, tool_result_msg])


def test_snapshot_shows_resumed_session_messages(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        # Wait for the app to initialize and rebuild history
        await pilot.pause(0.5)

    assert snap_compare(
        "test_ui_snapshot_session_resume.py:SnapshotTestAppWithResumedSession",
        terminal_size=(120, 36),
        run_before=run_before,
    )
