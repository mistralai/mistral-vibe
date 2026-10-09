from __future__ import annotations

import pytest
from textual import events
from textual.geometry import Offset
from textual.message import Message
from textual.selection import Selection

from tests.conftest import build_test_vibe_app
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer, ChatTextArea
from vibe.cli.textual_ui.widgets.chat_input.body import ChatInputBody
from vibe.cli.textual_ui.widgets.messages import UserMessage

OPTION_WORD_LEFT_KEYS = ["alt+left", "ctrl+left", "alt+b"]
OPTION_WORD_RIGHT_KEYS = ["alt+right", "ctrl+right", "alt+f"]


@pytest.mark.asyncio
async def test_undo_large_multiline_insert_does_not_crash() -> None:
    app = build_test_vibe_app()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.1)

        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        text_area.insert("line0\nline1\n")
        await pilot.pause(0.1)

        big_text = "\n".join(f"line{i}" for i in range(2, 50))
        text_area.insert(big_text)
        await pilot.pause(0.1)

        text_area.undo()
        await pilot.pause(0.1)

        assert text_area.text == "line0\nline1\n"


@pytest.mark.asyncio
async def test_shift_backspace_deletes_character_like_backspace() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        await pilot.press("a", "b", "c")
        await pilot.pause(0.1)
        assert app.query_one(ChatInputContainer).value == "abc"

        await pilot.press("shift+backspace")
        await pilot.pause(0.1)

        assert app.query_one(ChatInputContainer).value == "ab"


@pytest.mark.asyncio
async def test_shift_delete_deletes_character_like_delete() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        await pilot.press("a", "b", "c", "left")
        await pilot.pause(0.1)
        assert app.query_one(ChatInputContainer).value == "abc"

        await pilot.press("shift+delete")
        await pilot.pause(0.1)

        assert app.query_one(ChatInputContainer).value == "ab"


@pytest.mark.asyncio
async def test_shift_backspace_resets_mode_when_empty() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        text_area.set_mode("!")
        await pilot.pause(0.1)
        assert text_area.input_mode == "!"

        await pilot.press("shift+backspace")
        await pilot.pause(0.1)

        assert text_area.input_mode == ">"


@pytest.mark.asyncio
async def test_selection_cleared_when_focus_leaves_text_area() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause(0.1)

        text_area.load_text("hello world")
        text_area.select_all()
        await pilot.pause(0.1)
        assert text_area.selected_text == "hello world"

        app.query_one("#chat").focus()
        await pilot.pause(0.1)

        assert text_area.selected_text == ""


@pytest.mark.asyncio
async def test_blur_does_not_clear_other_widget_selection() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        text_area.load_text("hello world")
        text_area.select_all()
        await pilot.pause(0.1)

        chat = app.query_one("#chat")
        sentinel = {chat: Selection(Offset(0, 0), Offset(0, 1))}
        app.screen.selections = sentinel
        text_area.screen.set_focus(chat)
        await pilot.pause(0.1)

        assert text_area.selected_text == ""
        assert app.screen.selections == sentinel


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("left_key", "right_key"),
    zip(OPTION_WORD_LEFT_KEYS, OPTION_WORD_RIGHT_KEYS, strict=True),
)
async def test_option_left_and_option_right_move_by_word(
    left_key: str, right_key: str
) -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        chat_input = app.query_one(ChatInputContainer)
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        text_area.insert("hello brave world")
        text_area.move_cursor((0, len("hello brave world")))
        await pilot.pause()

        assert text_area.cursor_location == (0, len("hello brave world"))

        await pilot.press(left_key)
        assert text_area.cursor_location == (0, len("hello brave "))

        await pilot.press(left_key)
        assert text_area.cursor_location == (0, len("hello "))

        await pilot.press(right_key)
        assert text_area.cursor_location == (0, len("hello brave"))

        assert chat_input.value == "hello brave world"
        assert len(app.query(UserMessage)) == 0


@pytest.mark.parametrize("chord", ["ctrl+enter", "super+enter"])
@pytest.mark.asyncio
async def test_steer_chord_posts_request_when_input_empty(chord: str) -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause()

        posted: list[ChatTextArea.SteerQueueRequested] = []
        original = text_area.post_message

        def capture(message: Message) -> bool:
            if isinstance(message, ChatTextArea.SteerQueueRequested):
                posted.append(message)
            return original(message)

        text_area.post_message = capture  # type: ignore[method-assign]

        await pilot.press(chord)
        await pilot.pause()

        assert len(posted) == 1
        assert app.query_one(ChatInputContainer).value == ""


@pytest.mark.asyncio
async def test_steer_chord_does_nothing_when_input_has_text() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        text_area.insert("draft message")
        await pilot.pause()

        posted: list[ChatTextArea.SteerQueueRequested] = []
        submitted: list[ChatTextArea.Submitted] = []
        original = text_area.post_message

        def capture(message: Message) -> bool:
            if isinstance(message, ChatTextArea.SteerQueueRequested):
                posted.append(message)
            if isinstance(message, ChatTextArea.Submitted):
                submitted.append(message)
            return original(message)

        text_area.post_message = capture  # type: ignore[method-assign]

        await pilot.press("ctrl+enter")
        await pilot.pause()

        # A non-empty input must not steer, and the chord must not submit either.
        assert posted == []
        assert submitted == []
        assert app.query_one(ChatInputContainer).value == "draft message"


@pytest.mark.asyncio
async def test_emacs_ctrl_b_and_ctrl_f_move_by_character() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        text_area.insert("hello")
        await pilot.pause()

        await pilot.press("ctrl+b", "ctrl+b")
        assert text_area.cursor_location == (0, 3)

        await pilot.press("ctrl+f")
        assert text_area.cursor_location == (0, 4)
        assert app.query_one(ChatInputContainer).value == "hello"


@pytest.mark.asyncio
async def test_emacs_alt_d_deletes_word_right() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        text_area.insert("hello brave world")
        text_area.move_cursor((0, len("hello ")))
        await pilot.pause()

        # A terminal sends ESC d, which Textual parses with a printable character.
        app.post_message(events.Key("alt+d", "d"))
        await pilot.pause()

        assert app.query_one(ChatInputContainer).value == "hello  world"


@pytest.mark.asyncio
async def test_emacs_alt_b_and_alt_f_with_character_move_by_word() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        text_area.insert("hello brave world")
        await pilot.pause()

        app.post_message(events.Key("alt+b", "b"))
        await pilot.pause()
        assert text_area.cursor_location == (0, len("hello brave "))

        app.post_message(events.Key("alt+f", "f"))
        await pilot.pause()
        assert text_area.cursor_location == (0, len("hello brave world"))
        assert app.query_one(ChatInputContainer).value == "hello brave world"


@pytest.mark.asyncio
async def test_emacs_ctrl_underscore_undoes() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause()

        await pilot.press(*"hello")
        await pilot.press("ctrl+w")
        assert app.query_one(ChatInputContainer).value == ""

        await pilot.press("ctrl+underscore")

        assert app.query_one(ChatInputContainer).value == "hello"


@pytest.mark.asyncio
async def test_emacs_ctrl_p_and_ctrl_n_navigate_history() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        input_body = app.query_one(ChatInputBody)
        assert input_body.history is not None
        input_body.history.add("first prompt")
        input_body.history.add("second prompt")
        text_area.focus()
        await pilot.pause()

        await pilot.press("ctrl+p")
        await pilot.pause()
        assert text_area.text == "second prompt"

        await pilot.press("ctrl+p")
        await pilot.pause()
        assert text_area.text == "first prompt"

        await pilot.press("ctrl+n")
        await pilot.pause()
        assert text_area.text == "second prompt"


@pytest.mark.asyncio
async def test_emacs_ctrl_p_and_ctrl_n_move_between_lines() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        text_area.insert("first\nsecond\nthird")
        text_area.move_cursor((1, 2))
        await pilot.pause()

        await pilot.press("ctrl+p")
        await pilot.pause()
        assert text_area.cursor_location == (0, 2)

        await pilot.press("ctrl+n", "ctrl+n")
        await pilot.pause()
        assert text_area.cursor_location == (2, 2)
        assert app.query_one(ChatInputContainer).value == "first\nsecond\nthird"


@pytest.mark.asyncio
async def test_emacs_ctrl_h_acts_like_backspace() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        text_area = app.query_one(ChatTextArea)
        text_area.focus()
        text_area.set_mode("!")
        await pilot.press("a", "b")
        await pilot.pause()

        await pilot.press("ctrl+h")
        await pilot.pause()
        assert app.query_one(ChatInputContainer).value == "!a"

        await pilot.press("ctrl+h", "ctrl+h")
        await pilot.pause()
        assert text_area.input_mode == ">"
