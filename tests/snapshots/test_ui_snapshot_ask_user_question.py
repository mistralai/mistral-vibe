from __future__ import annotations

from textual.pilot import Pilot

from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp
from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.app_server import CoreEventProjection
from vibe.app_server.models import PublicEffectEntry
from vibe.cli.textual_ui.widgets.tools import ToolCallMessage, ToolResultMessage
from vibe.core.tools.builtins.ask_user_question import (
    AskUserQuestion,
    AskUserQuestionResult,
)
from vibe.core.types import ToolCallEvent, ToolResultEvent
from vibe.questions import (
    QuestionChoice,
    UserAnswer as Answer,
    UserQuestion,
    UserQuestionRequest,
)


class AskUserQuestionResultApp(BaseSnapshotTestApp):
    """Test app that displays an AskUserQuestion tool result."""

    async def on_ready(self) -> None:
        await super().on_ready()

        questions = [
            "What programming language are you currently working with?",
            "What type of project are you building?",
            "What editor or IDE do you prefer?",
        ]
        request = UserQuestionRequest(
            questions=[
                UserQuestion(
                    question=question,
                    options=[
                        QuestionChoice(label="First"),
                        QuestionChoice(label="Second"),
                    ],
                )
                for question in questions
            ]
        )
        result = AskUserQuestionResult(
            answers=[
                Answer(
                    question="What programming language are you currently working with?",
                    answer="Rust",
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

        projection = CoreEventProjection()
        projection.project(
            ToolCallEvent(
                tool_name="ask_user_question",
                tool_class=AskUserQuestion,
                args=request,
                tool_call_id="test_call_id",
            )
        )
        projection.project(
            ToolResultEvent(
                tool_name="ask_user_question",
                tool_class=AskUserQuestion,
                result=result,
                tool_call_id="test_call_id",
            )
        )
        entry = projection.history[-1]
        assert isinstance(entry, PublicEffectEntry)

        messages_area = self.query_one("#messages")
        # The real app always pairs a result with its call widget; the result
        # renders its wrapping "Answered <question> → <answer>" line onto it.
        call_widget = ToolCallMessage(entry)
        await messages_area.mount(call_widget)
        await messages_area.mount(ToolResultMessage(entry, call_widget))


def test_snapshot_ask_user_question(snap_compare: SnapCompare) -> None:
    """AskUserQuestion result is not collapsible: a single wrapping
    'Answered <question> → <answer>' line per answer.
    """

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_ask_user_question.py:AskUserQuestionResultApp",
        terminal_size=(120, 20),
        run_before=run_before,
    )
