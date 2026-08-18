from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.pilot import Pilot

from tests.conftest import build_test_agent_loop
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp, default_config
from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.app_server import CoreEventProjection
from vibe.app_server.models import PublicEffectEntry
from vibe.cli.textual_ui.widgets.status_message import StatusMessage
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage
from vibe.core.tools.builtins.edit import Edit, EditArgs, EditResult
from vibe.core.tools.builtins.read_file import ReadFile, ReadFileArgs, ReadFileResult
from vibe.core.tools.builtins.write_file import (
    WriteFile,
    WriteFileArgs,
    WriteFileResult,
)
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


def _nested(name: str = "config.py") -> str:
    return str(Path.cwd() / "src" / name)


# --- Minimal apps for call-display (running) snapshots ---


class _RelativePathApp(App):
    CSS_PATH = "../../vibe/cli/textual_ui/app.tcss"

    def __init__(self) -> None:
        super().__init__()
        self._widget: ToolCallMessage | None = None
        self._projection = CoreEventProjection()

    def _project(self, event: ToolCallEvent | ToolResultEvent) -> PublicEffectEntry:
        self._projection.project(event)
        entry = self._projection.history[-1]
        assert isinstance(entry, PublicEffectEntry)
        return entry


class ReadCallRelativePathApp(_RelativePathApp):
    def compose(self) -> ComposeResult:
        call = ToolCallEvent(
            tool_call_id="tc_read",
            tool_call_index=0,
            tool_name="read_file",
            tool_class=ReadFile,
            args=ReadFileArgs(file_path=_nested()),
        )
        self._widget = ToolCallMessage(self._project(call))
        with VerticalScroll():
            yield self._widget


class EditCallRelativePathApp(_RelativePathApp):
    def compose(self) -> ComposeResult:
        call = ToolCallEvent(
            tool_call_id="tc_edit",
            tool_call_index=0,
            tool_name="edit",
            tool_class=Edit,
            args=EditArgs(
                file_path=_nested(),
                old_string="MAX_USERS = 100",
                new_string="MAX_USERS = 200",
            ),
        )
        self._widget = ToolCallMessage(self._project(call))
        with VerticalScroll():
            yield self._widget


class WriteCallRelativePathApp(_RelativePathApp):
    def compose(self) -> ComposeResult:
        call = ToolCallEvent(
            tool_call_id="tc_write",
            tool_call_index=0,
            tool_name="write_file",
            tool_class=WriteFile,
            args=WriteFileArgs(file_path=_nested(), content="MAX_USERS = 200\n"),
        )
        self._widget = ToolCallMessage(self._project(call))
        with VerticalScroll():
            yield self._widget


# --- Full-app apps for result-display (settled) snapshots ---


def _read_result_messages() -> list[LLMMessage]:
    args = ReadFileArgs(file_path=_nested())
    result = ReadFileResult(
        file_path=_nested(), content="x = 1\n", num_lines=1, start_line=1, total_lines=1
    )
    call_event = ToolCallEvent(
        tool_name="read_file", tool_class=ReadFile, args=args, tool_call_id="tc_read"
    )
    result_event = ToolResultEvent(
        tool_name="read_file",
        tool_class=ReadFile,
        result=result,
        tool_call_id="tc_read",
    )
    presentation = ToolUIDataAdapter(ReadFile)
    return [
        LLMMessage(role=Role.user, content="Can you read my config?"),
        LLMMessage(
            role=Role.assistant,
            content="Let me read that file for you.",
            tool_calls=[
                ToolCall(
                    id="tc_read",
                    index=0,
                    function=FunctionCall(
                        name="read_file", arguments='{"file_path": "src/config.py"}'
                    ),
                    presentation=presentation.get_call_presentation(call_event),
                )
            ],
        ),
        LLMMessage(
            role=Role.tool,
            content="x = 1",
            name="read_file",
            tool_call_id="tc_read",
            tool_result=PersistedToolResult(
                output=result.model_dump(mode="json"),
                presentation=presentation.get_result_presentation(result_event),
            ),
        ),
    ]


def _edit_result_messages() -> list[LLMMessage]:
    args = EditArgs(
        file_path=_nested(), old_string="MAX_USERS = 100", new_string="MAX_USERS = 200"
    )
    result = EditResult(
        file=_nested(),
        message="The file has been updated successfully.",
        old_string="MAX_USERS = 100",
        new_string="MAX_USERS = 200",
    )
    call_event = ToolCallEvent(
        tool_name="edit", tool_class=Edit, args=args, tool_call_id="tc_edit"
    )
    result_event = ToolResultEvent(
        tool_name="edit", tool_class=Edit, result=result, tool_call_id="tc_edit"
    )
    presentation = ToolUIDataAdapter(Edit)
    return [
        LLMMessage(role=Role.user, content="Can you update the max users?"),
        LLMMessage(
            role=Role.assistant,
            content="I'll update that for you.",
            tool_calls=[
                ToolCall(
                    id="tc_edit",
                    index=0,
                    function=FunctionCall(
                        name="edit",
                        arguments='{"file_path": "src/config.py", "old_string": "MAX_USERS = 100", "new_string": "MAX_USERS = 200"}',
                    ),
                    presentation=presentation.get_call_presentation(call_event),
                )
            ],
        ),
        LLMMessage(
            role=Role.tool,
            content="The file has been updated successfully.",
            name="edit",
            tool_call_id="tc_edit",
            tool_result=PersistedToolResult(
                output=result.model_dump(mode="json"),
                presentation=presentation.get_result_presentation(result_event),
            ),
        ),
    ]


def _write_result_messages() -> list[LLMMessage]:
    args = WriteFileArgs(file_path=_nested(), content="MAX_USERS = 200\n")
    result = WriteFileResult(
        file_path=_nested(),
        bytes_written=len("MAX_USERS = 200\n"),
        content="MAX_USERS = 200\n",
    )
    call_event = ToolCallEvent(
        tool_name="write_file", tool_class=WriteFile, args=args, tool_call_id="tc_write"
    )
    result_event = ToolResultEvent(
        tool_name="write_file",
        tool_class=WriteFile,
        result=result,
        tool_call_id="tc_write",
    )
    presentation = ToolUIDataAdapter(WriteFile)
    return [
        LLMMessage(role=Role.user, content="Can you create a config file?"),
        LLMMessage(
            role=Role.assistant,
            content="I'll create that for you.",
            tool_calls=[
                ToolCall(
                    id="tc_write",
                    index=0,
                    function=FunctionCall(
                        name="write_file",
                        arguments='{"file_path": "src/config.py", "content": "MAX_USERS = 200\\n"}',
                    ),
                    presentation=presentation.get_call_presentation(call_event),
                )
            ],
        ),
        LLMMessage(
            role=Role.tool,
            content="File written successfully.",
            name="write_file",
            tool_call_id="tc_write",
            tool_result=PersistedToolResult(
                output=result.model_dump(mode="json"),
                presentation=presentation.get_result_presentation(result_event),
            ),
        ),
    ]


class ReadResultRelativePathApp(BaseSnapshotTestApp):
    def __init__(self) -> None:
        agent_loop = build_test_agent_loop(config=default_config())
        agent_loop.messages.extend(_read_result_messages())
        super().__init__(agent_loop=agent_loop)


class EditResultRelativePathApp(BaseSnapshotTestApp):
    def __init__(self) -> None:
        agent_loop = build_test_agent_loop(config=default_config())
        agent_loop.messages.extend(_edit_result_messages())
        super().__init__(agent_loop=agent_loop)


class WriteResultRelativePathApp(BaseSnapshotTestApp):
    def __init__(self) -> None:
        agent_loop = build_test_agent_loop(config=default_config())
        agent_loop.messages.extend(_write_result_messages())
        super().__init__(agent_loop=agent_loop)


# --- Call-display (running) snapshot tests ---


def test_snapshot_read_call_relative_path(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)

    with patch.object(StatusMessage, "start_spinner_timer"):
        assert snap_compare(
            "test_ui_snapshot_tool_call_relative_path.py:ReadCallRelativePathApp",
            terminal_size=(80, 10),
            run_before=run_before,
        )


def test_snapshot_edit_call_relative_path(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)

    with patch.object(StatusMessage, "start_spinner_timer"):
        assert snap_compare(
            "test_ui_snapshot_tool_call_relative_path.py:EditCallRelativePathApp",
            terminal_size=(80, 10),
            run_before=run_before,
        )


def test_snapshot_write_call_relative_path(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)

    with patch.object(StatusMessage, "start_spinner_timer"):
        assert snap_compare(
            "test_ui_snapshot_tool_call_relative_path.py:WriteCallRelativePathApp",
            terminal_size=(80, 10),
            run_before=run_before,
        )


# --- Result-display (settled) snapshot tests ---


def test_snapshot_read_result_relative_path(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.5)

    assert snap_compare(
        "test_ui_snapshot_tool_call_relative_path.py:ReadResultRelativePathApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_edit_result_relative_path(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.5)

    assert snap_compare(
        "test_ui_snapshot_tool_call_relative_path.py:EditResultRelativePathApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_write_result_relative_path(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.5)

    assert snap_compare(
        "test_ui_snapshot_tool_call_relative_path.py:WriteResultRelativePathApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )
