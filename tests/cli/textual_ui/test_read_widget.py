from __future__ import annotations

from vibe.cli.textual_ui.widgets.tool_widgets import _strip_line_numbers


def test_strips_numbered_prefixes() -> None:
    content = "        1→first\n       42→second\n      100→third"
    assert _strip_line_numbers(content) == "first\nsecond\nthird"


def test_leaves_warning_lines_untouched() -> None:
    content = "<vibe_warning>Warning: the file exists but the contents are empty.</vibe_warning>"
    assert _strip_line_numbers(content) == content


def test_preserves_arrows_inside_content() -> None:
    content = "        1→a → b → c"
    assert _strip_line_numbers(content) == "a → b → c"
