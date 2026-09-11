from __future__ import annotations

from pathlib import Path

import pytest
from textual import events
from textual.screen import ModalScreen
from textual.widgets import Static

from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
import vibe.cli.textual_ui.widgets.chat_input.paste_path as paste_path_module
from vibe.cli.textual_ui.widgets.chat_input.paste_path import (
    maybe_prepend_at_for_image_path,
    maybe_prepend_at_for_path,
    rewrite_bare_image_paths_in_text,
)
from vibe.cli.textual_ui.widgets.chat_input.text_area import ChatTextArea
from vibe.core.autocompletion.path_prompt import build_path_prompt_payload


def test_bare_absolute_image_path_gets_at_prefix(tmp_path: Path) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG")

    rewritten = maybe_prepend_at_for_image_path(str(img))

    assert rewritten == f"@{img}"


def test_quoted_image_path_with_spaces_is_unwrapped_and_quoted(tmp_path: Path) -> None:
    img = tmp_path / "has space.png"
    img.write_bytes(b"\x89PNG")
    pasted = f"'{img}'"

    rewritten = maybe_prepend_at_for_image_path(pasted)

    assert rewritten == f"@'{img}'"


def test_backslash_escaped_image_path_is_unescaped_and_quoted(tmp_path: Path) -> None:
    img = tmp_path / "has space.png"
    img.write_bytes(b"\x89PNG")
    pasted = str(img).replace(" ", "\\ ")

    rewritten = maybe_prepend_at_for_image_path(pasted)

    assert rewritten == f"@'{img}'"


def test_non_image_file_path_is_left_untouched(tmp_path: Path) -> None:
    txt = tmp_path / "readme.md"
    txt.write_text("hi")

    rewritten = maybe_prepend_at_for_image_path(str(txt))

    assert rewritten == str(txt)


def test_standalone_text_file_becomes_a_mention(tmp_path: Path) -> None:
    text_file = tmp_path / "notes.md"
    text_file.write_text("hi")

    assert maybe_prepend_at_for_path(str(text_file)) == f"@{text_file}"


def test_standalone_folder_becomes_a_mention(tmp_path: Path) -> None:
    folder = tmp_path / "docs"
    folder.mkdir()

    assert maybe_prepend_at_for_path(str(folder)) == f"@{folder}"


@pytest.mark.parametrize(
    "filename", ["note@draft.md", "report#final.md", "my'quoted\"note.md"]
)
def test_standalone_path_with_mention_syntax_is_quoted_and_attached(
    tmp_path: Path, filename: str
) -> None:
    path = tmp_path / filename
    path.write_text("", encoding="utf-8")

    mention = maybe_prepend_at_for_path(str(path))
    payload = build_path_prompt_payload(mention)

    assert mention.startswith("@'")
    assert [resource.path for resource in payload.resources] == [path]


def test_newline_delimited_path_list_becomes_mentions(tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("", encoding="utf-8")
    second.write_text("", encoding="utf-8")

    assert maybe_prepend_at_for_path(f"{first}\n{second}\n") == f"@{first} @{second}"


def test_standalone_missing_path_is_left_untouched(tmp_path: Path) -> None:
    pasted = str(tmp_path / "missing.md")

    assert maybe_prepend_at_for_path(pasted) == pasted


@pytest.mark.parametrize("name", ["src", "docs", "README.md"])
def test_standalone_relative_path_is_left_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    monkeypatch.chdir(tmp_path)
    path = tmp_path / name
    if path.suffix:
        path.write_text("", encoding="utf-8")
    else:
        path.mkdir()

    assert maybe_prepend_at_for_path(name) == name


def test_windows_path_keeps_backslashes_literal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "has\\ space.txt"
    path.write_text("", encoding="utf-8")
    monkeypatch.setattr(paste_path_module, "is_windows", lambda: True)

    assert maybe_prepend_at_for_path(str(path)) == f"@'{path}'"


def test_missing_image_path_is_left_untouched(tmp_path: Path) -> None:
    missing = tmp_path / "nope.png"

    rewritten = maybe_prepend_at_for_image_path(str(missing))

    assert rewritten == str(missing)


def test_unresolvable_tilde_user_does_not_crash() -> None:
    # `~a` raises RuntimeError from Path.expanduser() when user `a` does not
    # exist; the rewrite hook must swallow it so every keystroke after `~`
    # does not crash the TUI.
    assert maybe_prepend_at_for_image_path("~a") == "~a"
    assert rewrite_bare_image_paths_in_text("hello ~a world") == "hello ~a world"


def test_multiline_paste_is_left_untouched(tmp_path: Path) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG")
    pasted = f"{img}\nother line"

    assert maybe_prepend_at_for_image_path(pasted) == pasted


def test_relative_path_is_left_untouched(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "shot.png").write_bytes(b"\x89PNG")

    assert maybe_prepend_at_for_image_path("shot.png") == "shot.png"


def test_already_at_prefixed_path_is_left_untouched(tmp_path: Path) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG")

    pasted = f"@{img}"
    assert maybe_prepend_at_for_image_path(pasted) == pasted


@pytest.mark.asyncio
async def test_paste_event_inserts_at_prefixed_path_into_chat_input(
    vibe_app: VibeApp, tmp_path: Path
) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG")

    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        text_area = chat_input.query_one(ChatTextArea)
        text_area.focus()
        text_area.post_message(events.Paste(text=str(img)))
        await pilot.pause()

        assert chat_input.value == f"@{img}"


@pytest.mark.asyncio
async def test_paste_event_turns_text_file_into_a_mention(
    vibe_app: VibeApp, tmp_path: Path
) -> None:
    txt = tmp_path / "notes.md"
    txt.write_text("hi")

    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        text_area = chat_input.query_one(ChatTextArea)
        text_area.focus()
        text_area.post_message(events.Paste(text=str(txt)))
        await pilot.pause()

        assert chat_input.value == f"@{txt}"


def test_rewrite_bare_image_paths_handles_bare_path(tmp_path: Path) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG")

    rewritten = rewrite_bare_image_paths_in_text(f"look at {img} please")

    assert rewritten == f"look at @{img} please"


def test_rewrite_bare_image_paths_handles_quoted_path(tmp_path: Path) -> None:
    img = tmp_path / "has space.png"
    img.write_bytes(b"\x89PNG")

    rewritten = rewrite_bare_image_paths_in_text(f"look at '{img}'")

    assert rewritten == f"look at @'{img}'"


def test_rewrite_bare_image_paths_is_idempotent(tmp_path: Path) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG")

    once = rewrite_bare_image_paths_in_text(f"see {img}")
    twice = rewrite_bare_image_paths_in_text(once)

    assert once == twice == f"see @{img}"


def test_rewrite_bare_image_paths_skips_non_image(tmp_path: Path) -> None:
    txt = tmp_path / "notes.md"
    txt.write_text("hi")

    rewritten = rewrite_bare_image_paths_in_text(f"see {txt}")

    assert rewritten == f"see {txt}"


def test_rewrite_bare_image_paths_fast_path_skips_stat_for_plain_text(
    monkeypatch,
) -> None:
    from pathlib import Path as _Path

    calls = 0
    original = _Path.is_file

    def _counting_is_file(self):
        nonlocal calls
        calls += 1
        return original(self)

    monkeypatch.setattr(_Path, "is_file", _counting_is_file)

    rewrite_bare_image_paths_in_text("hello world, nothing path-shaped here")
    rewrite_bare_image_paths_in_text("multi\nline\ntext\nwith no slash")

    assert calls == 0


@pytest.mark.asyncio
async def test_text_change_hook_rewrites_quoted_image_path(
    vibe_app: VibeApp, tmp_path: Path
) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG")

    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        text_area = chat_input.query_one(ChatTextArea)
        text_area.focus()
        # Simulate a non-bracketed-paste insertion (terminal that does
        # not emit Paste): set the text directly the way a bulk insert
        # would land it.
        text_area.text = f"'{img}'"
        await pilot.pause()

        # No spaces in the path -> the scanner emits an unquoted `@<path>`.
        assert chat_input.value == f"@{img}"


@pytest.mark.asyncio
async def test_text_change_hook_rewrites_quoted_image_path_with_spaces(
    vibe_app: VibeApp, tmp_path: Path
) -> None:
    img = tmp_path / "has space.png"
    img.write_bytes(b"\x89PNG")

    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        text_area = chat_input.query_one(ChatTextArea)
        text_area.focus()
        text_area.text = f"'{img}'"
        await pilot.pause()

        assert chat_input.value == f"@'{img}'"


@pytest.mark.asyncio
async def test_paste_during_app_blur_forwards_to_chat_input(
    vibe_app: VibeApp, tmp_path: Path
) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG")

    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        text_area = chat_input.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause()

        # Simulate iTerm2 drag-and-drop: AppBlur → Paste → AppFocus.
        # The Paste arrives while no widget has focus (AppBlur cleared it),
        # which is the bug — Textual forwards to the screen instead of the
        # input widget.
        vibe_app.post_message(events.AppBlur())
        await pilot.pause()
        assert vibe_app.focused is None

        vibe_app.post_message(events.Paste(text=str(img)))
        await pilot.pause()

        vibe_app.post_message(events.AppFocus())
        await pilot.pause()

        assert chat_input.value == f"@{img}"


@pytest.mark.asyncio
async def test_paste_during_app_blur_forwards_text_file_as_a_mention(
    vibe_app: VibeApp, tmp_path: Path
) -> None:
    txt = tmp_path / "notes.md"
    txt.write_text("hi")

    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        text_area = chat_input.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause()

        vibe_app.post_message(events.AppBlur())
        await pilot.pause()
        assert vibe_app.focused is None

        vibe_app.post_message(events.Paste(text=str(txt)))
        await pilot.pause()

        vibe_app.post_message(events.AppFocus())
        await pilot.pause()

        assert chat_input.value == f"@{txt}"


@pytest.mark.asyncio
async def test_paste_during_app_blur_not_forwarded_when_modal_screen_open(
    vibe_app: VibeApp, tmp_path: Path
) -> None:
    txt = tmp_path / "notes.md"
    txt.write_text("hi")

    class _BlankModal(ModalScreen[None]):
        def compose(self):
            yield Static("modal")

    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        text_area = chat_input.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause()

        vibe_app.push_screen(_BlankModal())
        await pilot.pause()
        assert vibe_app.screen.is_modal

        vibe_app.post_message(events.AppBlur())
        await pilot.pause()

        vibe_app.post_message(events.Paste(text=str(txt)))
        await pilot.pause()

        vibe_app.post_message(events.AppFocus())
        await pilot.pause()

        assert chat_input.value == ""


@pytest.mark.asyncio
async def test_normal_paste_with_focused_widget_not_intercepted(
    vibe_app: VibeApp, tmp_path: Path
) -> None:
    txt = tmp_path / "notes.md"
    txt.write_text("hi")

    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        text_area = chat_input.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause()
        assert vibe_app.focused is not None

        vibe_app.post_message(events.Paste(text=str(txt)))
        await pilot.pause()

        assert chat_input.value == f"@{txt}"


@pytest.mark.asyncio
async def test_paste_during_app_blur_not_forwarded_when_input_disabled(
    vibe_app: VibeApp, tmp_path: Path
) -> None:
    txt = tmp_path / "notes.md"
    txt.write_text("hi")

    async with vibe_app.run_test() as pilot:
        chat_input = vibe_app.query_one(ChatInputContainer)
        text_area = chat_input.query_one(ChatTextArea)
        text_area.focus()
        await pilot.pause()

        chat_input.disabled = True
        await pilot.pause()

        vibe_app.post_message(events.AppBlur())
        await pilot.pause()
        assert vibe_app.focused is None

        vibe_app.post_message(events.Paste(text=str(txt)))
        await pilot.pause()

        vibe_app.post_message(events.AppFocus())
        await pilot.pause()

        assert chat_input.value == ""
