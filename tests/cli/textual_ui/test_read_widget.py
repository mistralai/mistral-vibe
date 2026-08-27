from __future__ import annotations

from pydantic import BaseModel
import pytest
from textual.content import Content

from vibe.app_server.models import (
    EffectCallDisplay,
    GenericEffectDetail,
    ShellEffectDetail,
    ShellEffectInput,
    ShellEffectOutput,
)
from vibe.cli.textual_ui.widgets.collapsible import CollapsibleSection
from vibe.cli.textual_ui.widgets.tool_widgets import (
    GenericToolResultWidget,
    ToolResultWidget,
    _fenced_code_block,
    _strip_line_numbers,
    clean_output,
    get_result_widget,
)


def test_clean_output_strips_ansi_and_control_bytes() -> None:
    out = clean_output("a\x1b[33mwarn\x1b[0mb\x1b[2Kc\x07d")
    assert "\x1b" not in out and "\x07" not in out
    assert out == "awarnbcd"


def test_clean_output_collapses_carriage_return_redraws() -> None:
    # uv-style in-place progress: keep only the final drawn state per line.
    content = "Preparing... (0/0)\rPreparing... (0/1)\rPreparing... (1/1)\ndone"
    assert clean_output(content) == "Preparing... (1/1)\ndone"


def test_clean_output_preserves_crlf_lines() -> None:
    assert clean_output("hello\r\nworld\r\n") == "hello\nworld\n"


def test_clean_output_keeps_a_line_parked_on_a_trailing_carriage_return() -> None:
    # Matches the webview: a trailing CR has not overwritten anything yet.
    assert clean_output("Progress: 50%\r") == "Progress: 50%"
    assert clean_output("10%\r100%\r") == "100%"


def test_clean_output_keeps_tabs_and_newlines() -> None:
    assert clean_output("a\tb\nc\td") == "a\tb\nc\td"


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("before\x1b]0;title\x07after", "beforeafter"),
        (
            "before\x1b]8;;https://example.com\x1b\\label\x1b]8;;\x1b\\after",
            "beforelabelafter",
        ),
    ],
)
def test_clean_output_strips_complete_osc_sequences(
    content: str, expected: str
) -> None:
    assert clean_output(content) == expected


def test_shell_result_widget_sanitizes_projected_output() -> None:
    detail = ShellEffectDetail(
        tool_name="bash",
        input=ShellEffectInput(command="status"),
        display=EffectCallDisplay(summary="status", status_text="running"),
    )
    widget = get_result_widget(
        detail,
        ShellEffectOutput(
            stdout="ready\rworking\x1b[2K\x07done", stderr="\x1b[31mwarning\x1b[0m"
        ).model_dump(mode="json", by_alias=True),
        True,
        "done",
    )

    rendered = next(iter(widget.compose())).render()
    assert isinstance(rendered, Content)
    assert rendered.plain == "workingdone\nwarning"


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
    detail = GenericEffectDetail(
        tool_name="unknown_tool",
        display=EffectCallDisplay(summary="unknown", status_text="running"),
    )
    widget = get_result_widget(detail, None, True, "done")
    assert type(widget) is GenericToolResultWidget


def test_default_widget_renders_fields_directly() -> None:
    """Result widgets yield content directly (no inner CollapsibleSection)."""

    class _Result(BaseModel):
        server: str
        text: str

    widget = ToolResultWidget(_Result(server="s", text="hello"), True, "ok")
    children = list(widget.compose())
    assert len(children) > 0
    assert not any(isinstance(child, CollapsibleSection) for child in children)
