from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.pilot import Pilot

from tests.snapshots.snap_compare import SnapCompare
from vibe.cli.textual_ui.widgets.question_app import QuestionApp
from vibe.core.tools.builtins.ask_user_question import (
    AskUserQuestionArgs,
    Choice,
    Question,
)


def single_question_args() -> AskUserQuestionArgs:
    return AskUserQuestionArgs(
        questions=[
            Question(
                question="Which database should we use for this project?",
                header="Database",
                options=[
                    Choice(label="PostgreSQL", description="Relational database"),
                    Choice(label="MongoDB", description="Document database"),
                    Choice(label="Redis", description="In-memory store"),
                ],
            )
        ]
    )


def multi_question_args() -> AskUserQuestionArgs:
    return AskUserQuestionArgs(
        questions=[
            Question(
                question="Which database?",
                header="DB",
                options=[Choice(label="PostgreSQL"), Choice(label="MongoDB")],
            ),
            Question(
                question="Which framework?",
                header="Framework",
                options=[Choice(label="FastAPI"), Choice(label="Django")],
            ),
        ]
    )


def multi_select_args() -> AskUserQuestionArgs:
    return AskUserQuestionArgs(
        questions=[
            Question(
                question="Which features do you want to enable?",
                header="Features",
                options=[
                    Choice(label="Authentication", description="User login/logout"),
                    Choice(label="Caching", description="Redis caching layer"),
                    Choice(label="Logging", description="Structured logging"),
                ],
                multi_select=True,
            )
        ]
    )


class QuestionAppTestApp(App):
    CSS_PATH = "../../vibe/cli/textual_ui/app.tcss"

    def __init__(self, args: AskUserQuestionArgs):
        super().__init__()
        self.question_args = args

    def compose(self) -> ComposeResult:
        with Container(id="bottom-app-container"):
            yield QuestionApp(args=self.question_args)


class SingleQuestionApp(QuestionAppTestApp):
    def __init__(self):
        super().__init__(single_question_args())


class MultiQuestionApp(QuestionAppTestApp):
    def __init__(self):
        super().__init__(multi_question_args())


class MultiSelectApp(QuestionAppTestApp):
    def __init__(self):
        super().__init__(multi_select_args())


# Single question tests


def test_snapshot_question_app_initial(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:SingleQuestionApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_question_app_navigate_down(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("down")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:SingleQuestionApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_question_app_navigate_to_third_option(
    snap_compare: SnapCompare,
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("down")
        await pilot.press("down")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:SingleQuestionApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_question_app_navigate_to_other(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("down")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:SingleQuestionApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_question_app_navigate_up_wraps(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("up")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:SingleQuestionApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_question_app_other_typing(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("down", "down", "down")
        await pilot.press("enter")
        await pilot.pause(0.1)
        await pilot.press(*"SQLite")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:SingleQuestionApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


# Multi-question tests


def test_snapshot_multi_question_initial(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:MultiQuestionApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_multi_question_tab_to_second(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("tab")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:MultiQuestionApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_multi_question_answer_first_advance(
    snap_compare: SnapCompare,
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:MultiQuestionApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_multi_question_navigate_right(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("right")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:MultiQuestionApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_multi_question_navigate_left_wraps(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("left")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:MultiQuestionApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_multi_question_first_answered_checkmark(
    snap_compare: SnapCompare,
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:MultiQuestionApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


# Multi-select tests


def test_snapshot_multi_select_initial(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:MultiSelectApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_multi_select_toggle_first(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:MultiSelectApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_multi_select_toggle_multiple(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.press("down", "down")
        await pilot.press("enter")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:MultiSelectApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_multi_select_navigate_to_submit(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("down", "down", "down", "down")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:MultiSelectApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_multi_select_other_with_text(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("down", "down", "down")
        await pilot.press("enter")
        await pilot.pause(0.1)
        await pilot.press(*"Custom feature")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:MultiSelectApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_multi_select_mixed_selection(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.press("down", "down")
        await pilot.press("enter")
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.1)
        await pilot.press(*"Extra")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:MultiSelectApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_multi_select_untoggle(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.press("enter")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_question_app.py:MultiSelectApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )
