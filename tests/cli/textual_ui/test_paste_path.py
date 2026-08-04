from __future__ import annotations

from pathlib import Path

import pytest
from textual import events

from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.chat_input.paste_path import (
    maybe_prepend_at_for_image_path,
    rewrite_bare_image_paths_in_text,
)
from vibe.cli.textual_ui.widgets.chat_input.text_area import ChatTextArea


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
async def test_paste_event_leaves_non_image_paths_untouched(
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

        assert chat_input.value == str(txt)


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
