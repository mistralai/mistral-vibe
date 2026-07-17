from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.pilot import Pilot

from tests.snapshots.snap_compare import SnapCompare
from vibe.cli.textual_ui.widgets.chat_input.completion_popup import CompletionPopup

FILE_MENTION_SUGGESTIONS: list[tuple[str, str]] = [
    ("@vibe/cli/textual_ui/widgets/chat_input/completion_popup.py", ""),
    ("@vibe/core/tools/builtins/very_long_deeply_nested_module_name.py", ""),
    ("@tests/snapshots/test_ui_snapshot_completion_popup_fixtures.py", ""),
]

SLASH_COMMAND_SUGGESTIONS: list[tuple[str, str]] = [
    (
        "/model",
        "Pick the model used for the conversation from every configured provider",
    ),
    (
        "/compact",
        "Summarize the conversation so far to reclaim context window headroom",
    ),
    (
        "/resume",
        "Reopen a previous local session and continue exactly where you left off",
    ),
]


class CompletionPopupTestApp(App):
    CSS_PATH = "../../vibe/cli/textual_ui/app.tcss"

    def compose(self) -> ComposeResult:
        with Container():
            yield CompletionPopup()


async def _show(pilot: Pilot, suggestions: list[tuple[str, str]]) -> None:
    pilot.app.query_one(CompletionPopup).update_suggestions(suggestions, selected=0)
    await pilot.pause(0.1)


def test_snapshot_completion_popup_file_mentions_stretch_full_width(
    snap_compare: SnapCompare,
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await _show(pilot, FILE_MENTION_SUGGESTIONS)

    assert snap_compare(
        "test_ui_snapshot_completion_popup.py:CompletionPopupTestApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )


def test_snapshot_completion_popup_slash_commands_two_columns(
    snap_compare: SnapCompare,
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await _show(pilot, SLASH_COMMAND_SUGGESTIONS)

    assert snap_compare(
        "test_ui_snapshot_completion_popup.py:CompletionPopupTestApp",
        terminal_size=(80, 20),
        run_before=run_before,
    )
