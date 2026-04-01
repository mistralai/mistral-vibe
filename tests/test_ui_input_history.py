from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibe.cli.history_manager import HistoryManager
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.body import ChatInputBody
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import UserMessage


@pytest.fixture
def history_file(tmp_path: Path) -> Path:
    history_file = tmp_path / "history.jsonl"
    history_entries = ["hello", "hi there", "how are you?"]
    history_file.write_text(
        "\n".join(json.dumps(entry) for entry in history_entries) + "\n",
        encoding="utf-8",
    )
    return history_file


def inject_history_file(vibe_app: VibeApp, history_file: Path) -> None:
    # Dependency Injection would help here, but as we don't have it yet: manual injection
    chat_input_body = vibe_app.query_one(ChatInputBody)
    chat_input_body.history = HistoryManager(history_file)


@pytest.mark.asyncio
async def test_ui_navigation_through_input_history(
    vibe_app: VibeApp, history_file: Path
) -> None:
    async with vibe_app.run_test() as pilot:
        inject_history_file(vibe_app, history_file)
        chat_input = vibe_app.query_one(ChatInputContainer)

        await pilot.press("up")
        assert chat_input.value == "how are you?"
        await pilot.press("up")
        assert chat_input.value == "hi there"
        await pilot.press("up")
        assert chat_input.value == "hello"
        await pilot.press("up")
        # cannot go further up
        assert chat_input.value == "hello"
        await pilot.press("down")
        assert chat_input.value == "hi there"
        await pilot.press("down")
        assert chat_input.value == "how are you?"
        await pilot.press("down")
        assert chat_input.value == ""


@pytest.mark.asyncio
async def test_ui_navigation_restores_partially_typed_draft_after_round_trip(
    vibe_app: VibeApp, history_file: Path
) -> None:
    async with vibe_app.run_test() as pilot:
        inject_history_file(vibe_app, history_file)
        chat_input = vibe_app.query_one(ChatInputContainer)

        await pilot.press(*"he")
        assert chat_input.value == "he"

        await pilot.press("up")
        assert chat_input.value == "how are you?"
        await pilot.press("down")
        assert chat_input.value == "he"


@pytest.mark.asyncio
async def test_ui_does_nothing_if_command_completion_is_active(
    vibe_app: VibeApp, history_file: Path
) -> None:
    async with vibe_app.run_test() as pilot:
        inject_history_file(vibe_app, history_file)
        chat_input = vibe_app.query_one(ChatInputContainer)

        await pilot.press("/")
        assert chat_input.value == "/"
        await pilot.press("up")
        assert chat_input.value == "/"
        await pilot.press("down")
        assert chat_input.value == "/"


@pytest.mark.asyncio
async def test_ui_does_not_prevent_arrow_down_to_move_cursor_to_bottom_lines(
    vibe_app: VibeApp,
):
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        textarea = chat_input.input_widget
        assert textarea is not None

        await pilot.press(*"test")
        await pilot.press("ctrl+j", "ctrl+j")
        assert chat_input.value == "test\n\n"
        assert textarea.text.count("\n") == 2
        initial_row = textarea.cursor_location[0]
        assert initial_row == 2, f"Expected cursor on line 2, got line {initial_row}"
        await pilot.press("up")
        assert textarea.cursor_location[0] == 1, "First arrow up should move to line 1"
        await pilot.press("up")
        assert textarea.cursor_location[0] == 0, (
            "Second arrow up should move to line 0 (first line)"
        )
        await pilot.press("down")
        final_row = textarea.cursor_location[0]
        assert final_row == 1, f"cursor is still on line {final_row}."


@pytest.mark.asyncio
async def test_ui_alt_left_and_alt_right_move_by_word(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        textarea = chat_input.input_widget
        assert textarea is not None

        await pilot.press(*"hello brave world")
        assert textarea.cursor_location == (0, len("hello brave world"))

        await pilot.press("alt+left")
        assert textarea.cursor_location == (0, len("hello brave "))

        await pilot.press("alt+left")
        assert textarea.cursor_location == (0, len("hello "))

        await pilot.press("alt+right")
        assert textarea.cursor_location == (0, len("hello brave"))

        assert chat_input.value == "hello brave world"
        assert len(vibe_app.query(UserMessage)) == 0


@pytest.mark.asyncio
async def test_ui_resumes_arrow_down_after_manual_move(
    vibe_app: VibeApp, tmp_path: Path
) -> None:
    history_path = tmp_path / "history.jsonl"
    history_path.write_text(
        json.dumps("first line\nsecond line") + "\n", encoding="utf-8"
    )

    async with vibe_app.run_test() as pilot:
        inject_history_file(vibe_app, history_path)
        chat_input = vibe_app.query_one(ChatInputContainer)
        textarea = chat_input.input_widget
        assert textarea is not None

        await pilot.press("up")
        assert chat_input.value == "first line\nsecond line"
        assert textarea.cursor_location == (0, len("first line"))
        await pilot.press("left")
        await pilot.press("down")
        assert textarea.cursor_location[0] == 1
        assert chat_input.value == "first line\nsecond line"


@pytest.mark.asyncio
async def test_ui_does_not_intercept_arrow_down_inside_wrapped_single_line_input(
    vibe_app: VibeApp,
) -> None:
    long_input = "0123456789 " * 20

    async with vibe_app.run_test(size=(40, 20)) as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        textarea = chat_input.input_widget
        assert textarea is not None

        textarea.insert(long_input)
        assert chat_input.value == long_input
        assert textarea.wrapped_document.height > 1

        textarea.action_cursor_up()
        location_after_up = textarea.cursor_location
        assert textarea.get_cursor_down_location() != location_after_up

        await pilot.press("down")

        assert chat_input.value == long_input
        assert textarea.cursor_location != location_after_up


@pytest.mark.asyncio
async def test_ui_intercepts_arrow_up_only_on_first_wrapped_row(
    vibe_app: VibeApp, history_file: Path
) -> None:
    long_input = "abcdefghij " * 20

    async with vibe_app.run_test(size=(40, 20)) as pilot:
        inject_history_file(vibe_app, history_file)
        chat_input = vibe_app.query_one(ChatInputContainer)
        textarea = chat_input.input_widget
        assert textarea is not None

        textarea.insert(long_input)
        assert chat_input.value == long_input
        assert textarea.wrapped_document.height > 1

        textarea.action_cursor_up()
        assert chat_input.value == long_input

        while textarea.get_cursor_up_location() != textarea.cursor_location:
            textarea.action_cursor_up()

        await pilot.press("up")
        assert chat_input.value == "how are you?"


@pytest.mark.asyncio
async def test_ui_up_from_wrapped_top_loads_history_after_down_at_wrapped_bottom(
    vibe_app: VibeApp, history_file: Path
) -> None:
    long_input = "LONG " + ("x" * 160)

    async with vibe_app.run_test(size=(40, 20)) as pilot:
        inject_history_file(vibe_app, history_file)
        chat_input = vibe_app.query_one(ChatInputContainer)
        textarea = chat_input.input_widget
        assert textarea is not None

        textarea.insert(long_input)
        assert chat_input.value == long_input
        assert not textarea.navigator.is_first_wrapped_line(textarea.cursor_location)
        assert textarea.navigator.is_last_wrapped_line(textarea.cursor_location)

        await pilot.press("down")
        textarea.move_cursor((0, 0))
        assert textarea.navigator.is_first_wrapped_line(textarea.cursor_location)

        await pilot.press("up")

        assert chat_input.value == "how are you?"


@pytest.mark.asyncio
async def test_ui_down_cycles_to_next_history_without_manual_move_after_loading_multiline_entry(
    vibe_app: VibeApp, tmp_path: Path
) -> None:
    long_first_line = "abcdefghij " * 20
    history_entry = f"{long_first_line}\nsecond line"
    history_path = tmp_path / "history.jsonl"
    history_path.write_text(json.dumps(history_entry) + "\n", encoding="utf-8")

    async with vibe_app.run_test(size=(40, 20)) as pilot:
        inject_history_file(vibe_app, history_path)
        chat_input = vibe_app.query_one(ChatInputContainer)
        textarea = chat_input.input_widget
        assert textarea is not None

        await pilot.press("up")
        assert chat_input.value == history_entry
        assert not textarea.navigator.is_first_wrapped_line(textarea.cursor_location)
        assert not textarea.navigator.is_last_wrapped_line(textarea.cursor_location)

        await pilot.press("down")
        assert chat_input.value == ""


@pytest.mark.asyncio
async def test_ui_up_continues_history_cycle_after_loading_wrapped_multiline_entry(
    vibe_app: VibeApp, tmp_path: Path
) -> None:
    long_first_line = "abcdefghij " * 20
    wrapped_multiline = f"{long_first_line}\nsecond line"
    history_path = tmp_path / "history.jsonl"
    history_entries = ["older message", wrapped_multiline, "Hi there"]
    history_path.write_text(
        "\n".join(json.dumps(entry) for entry in history_entries) + "\n",
        encoding="utf-8",
    )

    async with vibe_app.run_test(size=(40, 20)) as pilot:
        inject_history_file(vibe_app, history_path)
        chat_input = vibe_app.query_one(ChatInputContainer)
        textarea = chat_input.input_widget
        assert textarea is not None

        await pilot.press("up")
        assert chat_input.value == "Hi there"

        await pilot.press("up")
        assert chat_input.value == wrapped_multiline
        assert not textarea.navigator.is_first_wrapped_line(textarea.cursor_location)

        await pilot.press("up")
        assert chat_input.value == "older message"


@pytest.mark.asyncio
async def test_ui_down_at_visual_end_resumes_history_after_manual_cursor_move(
    vibe_app: VibeApp, tmp_path: Path
) -> None:
    long_first_line = "abcdefghij " * 20
    wrapped_multiline = f"{long_first_line}\nsecond line"
    history_path = tmp_path / "history.jsonl"
    history_entries = ["older message", wrapped_multiline, "Hi there"]
    history_path.write_text(
        "\n".join(json.dumps(entry) for entry in history_entries) + "\n",
        encoding="utf-8",
    )

    async with vibe_app.run_test(size=(40, 20)) as pilot:
        inject_history_file(vibe_app, history_path)
        chat_input = vibe_app.query_one(ChatInputContainer)
        textarea = chat_input.input_widget
        assert textarea is not None

        await pilot.press("up")
        await pilot.press("up")
        assert chat_input.value == wrapped_multiline

        await pilot.press("left")
        assert textarea.cursor_location != (0, len(long_first_line))

        while not textarea.navigator.is_last_wrapped_line(textarea.cursor_location):
            textarea.action_cursor_down()
        textarea.move_cursor((1, len("second line")))
        assert textarea.navigator.is_last_wrapped_line(textarea.cursor_location)

        await pilot.press("down")
        assert chat_input.value == "Hi there"
