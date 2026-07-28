from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from vibe.cli.textual_ui.app import ChatScroll
from vibe.cli.textual_ui.widgets.collapsible import (
    HeaderCollapsibleSection,
    _single_line,
)
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic


class _HeaderApp(App[None]):
    CSS_PATH = Path(__file__).parents[3] / "vibe/cli/textual_ui/app.tcss"

    def __init__(self, header_text: str) -> None:
        super().__init__()
        self.section = HeaderCollapsibleSection(
            NoMarkupStatic("body"),
            header_verb="Read",
            header_text=header_text,
            header_suffix="(scratchpad)",
        )

    def compose(self) -> ComposeResult:
        yield self.section


class _ScrollableHeaderApp(App[None]):
    CSS_PATH = Path(__file__).parents[3] / "vibe/cli/textual_ui/app.tcss"

    def __init__(self) -> None:
        super().__init__()
        self.section = HeaderCollapsibleSection(
            NoMarkupStatic("\n".join(["body"] * 20)), header_text="expand"
        )

    def compose(self) -> ComposeResult:
        with ChatScroll(id="chat"):
            yield NoMarkupStatic("\n".join(["filler"] * 20))
            yield self.section


def test_single_line_flattens_multiline_command() -> None:
    command = "cat > /tmp/pr_body.md <<'EOF'\n## Context\n\n- item\nEOF"
    flattened = _single_line(command)
    assert "\n" not in flattened
    assert flattened.startswith("cat > /tmp/pr_body.md <<'EOF' ## Context")


def test_single_line_strips_control_and_escape_bytes() -> None:
    # The ESC bytes are removed so the sequence can never execute in the
    # terminal; the remaining printable payload is left as harmless text.
    out = _single_line("a\x1b]11;rgb:1e1e/1e1e/2e2e\x1b\\b")
    assert "\x1b" not in out
    assert out == "a]11;rgb:1e1e/1e1e/2e2e\\b"
    assert _single_line("keep\ttabs\rand\rnewlines") == "keep tabs and newlines"


def test_section_stores_single_line_header() -> None:
    section = HeaderCollapsibleSection(
        NoMarkupStatic("body"), header_text="line one\nline two"
    )
    assert section._header_text == "line one line two"
    assert "\n" not in section._header_text


@pytest.mark.asyncio
async def test_suffix_follows_short_header_message() -> None:
    app = _HeaderApp("3 lines from dummy.txt")

    async with app.run_test(size=(80, 10)) as pilot:
        await pilot.pause()

        text = app.query_one(".status-indicator-text", NoMarkupStatic)
        suffix = app.query_one(".status-indicator-suffix", NoMarkupStatic)
        assert suffix.region.x == text.region.right + suffix.styles.margin.left


@pytest.mark.asyncio
async def test_expanding_section_preserves_scroll_position() -> None:
    app = _ScrollableHeaderApp()

    async with app.run_test(size=(80, 10)) as pilot:
        chat = app.query_one("#chat", ChatScroll)
        chat.anchor()
        await pilot.pause()
        initial_scroll_y = chat.scroll_y
        initial_header_y = app.section._toggle_row.region.y
        assert chat.is_at_bottom

        app.section.set_collapsed(False)
        await pilot.pause()

        assert chat.scroll_y == initial_scroll_y
        assert app.section._toggle_row.region.y == initial_header_y
        assert not chat.is_at_bottom

        chat.scroll_relative(y=chat.max_scroll_y, animate=False)
        await pilot.pause()
        assert chat.is_at_bottom

        await chat.mount(NoMarkupStatic("new content"))
        await pilot.pause()
        assert chat.is_at_bottom
