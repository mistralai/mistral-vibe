from __future__ import annotations

from textual.pilot import Pilot

from tests.conftest import build_test_agent_loop
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp, default_config
from tests.snapshots.snap_compare import SnapCompare
from vibe.core.tools.builtins.read_file import ReadFile, ReadFileArgs, ReadFileResult
from vibe.core.tools.ui import ToolUIDataAdapter
from vibe.core.types import (
    FunctionCall,
    LLMMessage,
    PersistedToolResult,
    Role,
    ToolCall,
    ToolCallEvent,
    ToolResultEvent,
)


class SnapshotTestAppWithResumedSession(BaseSnapshotTestApp):
    def __init__(self) -> None:
        agent_loop = build_test_agent_loop(config=default_config())
        args = ReadFileArgs(file_path="test.txt")
        result = ReadFileResult(
            file_path="test.txt",
            content="File content: This is a test file with some content.",
            num_lines=1,
            start_line=1,
        )
        call_event = ToolCallEvent(
            tool_name="read", tool_class=ReadFile, args=args, tool_call_id="tool_call_1"
        )
        result_event = ToolResultEvent(
            tool_name="read",
            tool_class=ReadFile,
            result=result,
            tool_call_id="tool_call_1",
        )
        presentation = ToolUIDataAdapter(ReadFile)
        user_msg = LLMMessage(role=Role.user, content="Hello, how are you?")
        assistant_msg = LLMMessage(
            role=Role.assistant,
            content="I'm doing well, thank you! Let me read that file for you.",
            tool_calls=[
                ToolCall(
                    id="tool_call_1",
                    index=0,
                    function=FunctionCall(
                        name="read", arguments='{"file_path": "test.txt"}'
                    ),
                    presentation=presentation.get_call_presentation(call_event),
                )
            ],
        )
        tool_result_msg = LLMMessage(
            role=Role.tool,
            content="File content: This is a test file with some content.",
            name="read",
            tool_call_id="tool_call_1",
            tool_result=PersistedToolResult(
                output=result.model_dump(mode="json"),
                presentation=presentation.get_result_presentation(result_event),
            ),
        )

        agent_loop.messages.extend([user_msg, assistant_msg, tool_result_msg])
        super().__init__(agent_loop=agent_loop)


def test_snapshot_shows_resumed_session_messages(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        # Wait for the app to initialize and rebuild history
        await pilot.pause(0.5)

    assert snap_compare(
        "test_ui_snapshot_session_resume.py:SnapshotTestAppWithResumedSession",
        terminal_size=(120, 36),
        run_before=run_before,
    )
