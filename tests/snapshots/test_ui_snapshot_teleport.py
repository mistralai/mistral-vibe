from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.pilot import Pilot

from tests.snapshots.snap_compare import SnapCompare
from vibe.cli.textual_ui.widgets.question_app import QuestionApp
from vibe.cli.textual_ui.widgets.teleport_message import TeleportMessage
from vibe.core.tools.builtins.ask_user_question import (
    AskUserQuestionArgs,
    Choice,
    Question,
)


class TeleportMessageTestApp(App):
    CSS_PATH = "../../vibe/cli/textual_ui/app.tcss"

    def compose(self) -> ComposeResult:
        with Container():
            yield TeleportMessage()


class TeleportMessageCheckingGitApp(TeleportMessageTestApp):
    def on_mount(self) -> None:
        widget = self.query_one(TeleportMessage)
        widget.set_status("Checking git status...")
        if widget._spinner_timer:
            widget._spinner_timer.stop()


class TeleportMessagePushingApp(TeleportMessageTestApp):
    def on_mount(self) -> None:
        widget = self.query_one(TeleportMessage)
        widget.set_status("Pushing to remote...")
        if widget._spinner_timer:
            widget._spinner_timer.stop()


class TeleportMessageAuthRequiredApp(TeleportMessageTestApp):
    def on_mount(self) -> None:
        widget = self.query_one(TeleportMessage)
        widget.set_status(
            "GitHub auth required. Code: ABCD-1234 (copied)\nOpen: https://github.com/login/device"
        )
        if widget._spinner_timer:
            widget._spinner_timer.stop()


class TeleportMessageAuthCompleteApp(TeleportMessageTestApp):
    def on_mount(self) -> None:
        widget = self.query_one(TeleportMessage)
        widget.set_status("GitHub authenticated.")
        if widget._spinner_timer:
            widget._spinner_timer.stop()


class TeleportMessageStartingWorkflowApp(TeleportMessageTestApp):
    def on_mount(self) -> None:
        widget = self.query_one(TeleportMessage)
        widget.set_status("Starting Nuage workflow...")
        if widget._spinner_timer:
            widget._spinner_timer.stop()


class TeleportMessageSendingTokenApp(TeleportMessageTestApp):
    def on_mount(self) -> None:
        widget = self.query_one(TeleportMessage)
        widget.set_status("Sending encrypted GitHub token...")
        if widget._spinner_timer:
            widget._spinner_timer.stop()


class TeleportMessageCompleteApp(TeleportMessageTestApp):
    def on_mount(self) -> None:
        widget = self.query_one(TeleportMessage)
        widget.set_complete("https://chat.example.com")


class TeleportMessageErrorApp(TeleportMessageTestApp):
    def on_mount(self) -> None:
        widget = self.query_one(TeleportMessage)
        widget.set_error("Git repository has uncommitted changes")


def _push_confirmation_args(count: int) -> AskUserQuestionArgs:
    word = f"commit{'s' if count != 1 else ''}"
    return AskUserQuestionArgs(
        questions=[
            Question(
                question=f"You have {count} unpushed {word}. Push to continue?",
                header="Push",
                options=[Choice(label="Push and continue"), Choice(label="Cancel")],
                hide_other=True,
            )
        ]
    )


class TeleportPushConfirmationTestApp(App):
    CSS_PATH = "../../vibe/cli/textual_ui/app.tcss"

    def __init__(self, count: int = 3):
        super().__init__()
        self.count = count

    def compose(self) -> ComposeResult:
        with Container(id="bottom-app-container"):
            yield QuestionApp(args=_push_confirmation_args(self.count))


class TeleportPushConfirmationSingleCommitApp(TeleportPushConfirmationTestApp):
    def __init__(self):
        super().__init__(count=1)


class TeleportPushConfirmationMultipleCommitsApp(TeleportPushConfirmationTestApp):
    def __init__(self):
        super().__init__(count=5)


def test_snapshot_teleport_status_checking_git(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_teleport.py:TeleportMessageCheckingGitApp",
        terminal_size=(80, 10),
        run_before=run_before,
    )


def test_snapshot_teleport_status_pushing(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_teleport.py:TeleportMessagePushingApp",
        terminal_size=(80, 10),
        run_before=run_before,
    )


def test_snapshot_teleport_status_auth_required(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_teleport.py:TeleportMessageAuthRequiredApp",
        terminal_size=(80, 10),
        run_before=run_before,
    )


def test_snapshot_teleport_status_auth_complete(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_teleport.py:TeleportMessageAuthCompleteApp",
        terminal_size=(80, 10),
        run_before=run_before,
    )


def test_snapshot_teleport_status_starting_workflow(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_teleport.py:TeleportMessageStartingWorkflowApp",
        terminal_size=(80, 10),
        run_before=run_before,
    )


def test_snapshot_teleport_status_sending_token(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_teleport.py:TeleportMessageSendingTokenApp",
        terminal_size=(80, 10),
        run_before=run_before,
    )


def test_snapshot_teleport_status_complete(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_teleport.py:TeleportMessageCompleteApp",
        terminal_size=(80, 10),
        run_before=run_before,
    )


def test_snapshot_teleport_status_error(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_teleport.py:TeleportMessageErrorApp",
        terminal_size=(80, 10),
        run_before=run_before,
    )


def test_snapshot_teleport_push_confirmation_single_commit(
    snap_compare: SnapCompare,
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_teleport.py:TeleportPushConfirmationSingleCommitApp",
        terminal_size=(80, 12),
        run_before=run_before,
    )


def test_snapshot_teleport_push_confirmation_multiple_commits(
    snap_compare: SnapCompare,
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_teleport.py:TeleportPushConfirmationMultipleCommitsApp",
        terminal_size=(80, 12),
        run_before=run_before,
    )


def test_snapshot_teleport_push_confirmation_cancel_selected(
    snap_compare: SnapCompare,
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.1)
        await pilot.press("down")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_teleport.py:TeleportPushConfirmationMultipleCommitsApp",
        terminal_size=(80, 12),
        run_before=run_before,
    )
