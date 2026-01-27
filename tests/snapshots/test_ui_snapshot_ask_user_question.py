from __future__ import annotations

from textual.pilot import Pilot

from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from vibe.cli.textual_ui.widgets.tools import ToolResultMessage
from vibe.core.tools.builtins.ask_user_question import (
    Answer,
    AskUserQuestion,
    AskUserQuestionResult,
)
from vibe.core.types import ToolResultEvent


class AskUserQuestionResultApp(BaseSnapshotTestApp):
    """Test app that displays an AskUserQuestion tool result."""

    async def on_mount(self) -> None:
        await super().on_mount()

        result = AskUserQuestionResult(
            answers=[
                Answer(
                    question="What programming language are you currently working with?",
                    answer="Rust",
                    is_other=False,
                ),
                Answer(
                    question="What type of project are you building?",
                    answer="Web Application",
                    is_other=False,
                ),
                Answer(
                    question="What editor or IDE do you prefer?",
                    answer="VS Code",
                    is_other=True,
                ),
            ],
            cancelled=False,
        )

        event = ToolResultEvent(
            tool_name="ask_user_question",
            tool_class=AskUserQuestion,
            result=result,
            tool_call_id="test_call_id",
        )

        messages_area = self.query_one("#messages")
        tool_result = ToolResultMessage(event, collapsed=True)
        await messages_area.mount(tool_result)


def test_snapshot_ask_user_question_collapsed(snap_compare: SnapCompare) -> None:
    """Test collapsed AskUserQuestion result shows summary."""

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_ask_user_question.py:AskUserQuestionResultApp",
        terminal_size=(120, 20),
        run_before=run_before,
    )


def test_snapshot_ask_user_question_expanded(snap_compare: SnapCompare) -> None:
    """Test expanded AskUserQuestion result shows formatted Q&A pairs."""

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("ctrl+o")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_ask_user_question.py:AskUserQuestionResultApp",
        terminal_size=(120, 30),
        run_before=run_before,
    )
