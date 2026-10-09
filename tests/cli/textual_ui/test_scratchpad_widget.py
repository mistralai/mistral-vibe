from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.content import Content
from textual.widgets import Markdown, Static

from vibe.app_server.models import (
    EffectCallDisplay,
    ScratchpadEffectDetail,
    ScratchpadEffectOutput,
    ScratchpadListInput,
    ScratchpadWriteInput,
)
from vibe.cli.textual_ui.widgets.tool_widgets import (
    ScratchpadResultWidget,
    ToolResultWidget,
    get_result_widget,
)

_DISPLAY = EffectCallDisplay(summary="scratchpad", status_text="Using the scratchpad")


class _ResultApp(App[None]):
    def __init__(self, widget: ToolResultWidget) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


def _widget(
    detail: ScratchpadEffectDetail, output: ScratchpadEffectOutput
) -> ToolResultWidget:
    widget = get_result_widget(
        detail, output.model_dump(mode="json"), True, "scratchpad"
    )
    assert isinstance(widget, ScratchpadResultWidget)
    return widget


@pytest.mark.asyncio
async def test_a_written_note_renders_as_a_fenced_code_block() -> None:
    note = "# Plan\n\n- ship it\n"
    detail = ScratchpadEffectDetail(
        tool_name="vibe.unified_harness_scratchpad",
        input=ScratchpadWriteInput(path="plan.md", content=note),
        display=_DISPLAY,
    )
    widget = _widget(detail, ScratchpadEffectOutput(path="plan.md", content=note))

    app = _ResultApp(widget)
    async with app.run_test() as pilot:
        await pilot.pause()
        source = app.query_one(Markdown).source

    assert source.startswith("```")
    assert "# Plan\n\n- ship it" in source
    assert "structured_content" not in source


def test_a_listed_scratchpad_renders_one_file_name_per_row() -> None:
    detail = ScratchpadEffectDetail(
        tool_name="vibe.unified_harness_scratchpad",
        input=ScratchpadListInput(),
        display=_DISPLAY,
    )
    widget = _widget(detail, ScratchpadEffectOutput(files=["plan.md", "notes/api.md"]))

    rendered = next(
        child for child in widget.compose() if isinstance(child, Static)
    ).render()

    assert isinstance(rendered, Content)
    assert rendered.plain == "plan.md\nnotes/api.md"


def test_an_empty_scratchpad_list_renders_no_body() -> None:
    detail = ScratchpadEffectDetail(
        tool_name="vibe.unified_harness_scratchpad",
        input=ScratchpadListInput(),
        display=_DISPLAY,
    )
    widget = _widget(detail, ScratchpadEffectOutput())

    assert list(widget.compose()) == []
