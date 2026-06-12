from __future__ import annotations

from pydantic import BaseModel

from vibe.cli.textual_ui.widgets.collapsible import CollapsibleSection
from vibe.cli.textual_ui.widgets.tool_widgets import (
    ToolResultWidget,
    _fenced_code_block,
    _strip_line_numbers,
    get_result_widget,
)


def test_strips_numbered_prefixes() -> None:
    content = "        1→first\n       42→second\n      100→third"
    assert _strip_line_numbers(content) == "first\nsecond\nthird"


def test_leaves_warning_lines_untouched() -> None:
    content = "<vibe_warning>Warning: the file exists but the contents are empty.</vibe_warning>"
    assert _strip_line_numbers(content) == content


def test_preserves_arrows_inside_content() -> None:
    content = "        1→a → b → c"
    assert _strip_line_numbers(content) == "a → b → c"


def test_fence_uses_three_backticks_for_plain_content() -> None:
    block = _fenced_code_block("hello\nworld", "py")
    assert block == "```py\nhello\nworld\n```"


def test_fence_outgrows_embedded_triple_backticks() -> None:
    content = "before\n```\n[click me](http://evil)\n```\nafter"
    block = _fenced_code_block(content, "md")
    fence = "````"
    assert block == f"{fence}md\n{content}\n{fence}"
    assert block.startswith(fence)
    assert block.endswith(fence)


def test_fence_outgrows_longest_backtick_run() -> None:
    content = "a ```` b ``` c"
    block = _fenced_code_block(content, "")
    assert block.startswith("`````")
    assert block.endswith("`````")


def test_fence_strips_newlines_from_ext() -> None:
    block = _fenced_code_block("safe", "x\n[click](http://evil)")
    assert block == "```xclickhttpevil\nsafe\n```"
    assert "\n[click]" not in block


def test_fence_strips_backticks_from_ext() -> None:
    block = _fenced_code_block("safe", "py`\n```md")
    first_line = block.split("\n", 1)[0]
    assert first_line == "```pymd"


def test_fence_caps_ext_length() -> None:
    block = _fenced_code_block("safe", "a" * 500)
    first_line = block.split("\n", 1)[0]
    assert first_line == "```" + "a" * 32


def test_unknown_tool_uses_default_widget() -> None:
    widget = get_result_widget("unknown_tool", None, True, "done")
    assert type(widget) is ToolResultWidget


def test_default_widget_renders_fields_collapsibly() -> None:
    class _Result(BaseModel):
        server: str
        text: str

    widget = ToolResultWidget(_Result(server="s", text="hello"), True, "ok")
    children = list(widget.compose())
    assert any(isinstance(child, CollapsibleSection) for child in children)
