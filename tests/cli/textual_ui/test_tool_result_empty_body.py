from __future__ import annotations

from pydantic import JsonValue
import pytest
from textual.app import App, ComposeResult
from textual.containers import Vertical

from vibe.app_server.models import (
    CompletedEffectState,
    EffectCallDisplay,
    EffectResultDisplay,
    GenericEffectDetail,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
)
from vibe.cli.textual_ui.widgets.collapsible import HeaderCollapsibleSection
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.tools import ToolResultMessage


def _completed_entry(output: JsonValue, output_text: str) -> PublicEffectEntry:
    return PublicEffectEntry(
        id="call-1",
        session_id="s",
        turn_id="t",
        created_at=1,
        updated_at=1,
        generation_status=PublicEntryGenerationStatus.COMPLETED,
        title="bash",
        detail=GenericEffectDetail(
            tool_name="bash",
            display=EffectCallDisplay(summary="bash", status_text="Running bash"),
        ),
        state=CompletedEffectState(
            output=output,
            output_text=output_text,
            display=EffectResultDisplay(success=True, message="echo plop"),
        ),
    )


class _ResultApp(App[None]):
    def __init__(self, entry: PublicEffectEntry) -> None:
        super().__init__()
        self._entry = entry
        self.result: ToolResultMessage | None = None

    def compose(self) -> ComposeResult:
        yield Vertical(id="root")

    async def on_mount(self) -> None:
        root = self.query_one("#root", Vertical)
        self.result = ToolResultMessage(self._entry)
        await root.mount(self.result)


@pytest.mark.asyncio
async def test_hook_replaced_result_is_expandable_and_shows_the_reason() -> None:
    # A post_tool deny leaves no structured output but keeps the reason in output_text;
    # the header stays expandable and unfolds to show that reason.
    app = _ResultApp(_completed_entry(None, "Blocked by deny-plop: contained plop"))
    async with app.run_test() as pilot:
        await pilot.pause()
        result = app.result
        assert result is not None
        section = result.query_one(HeaderCollapsibleSection)
        assert section._collapsible is True

        section.set_collapsed(False)
        await pilot.pause()

        texts = [str(w.render()) for w in section.query(NoMarkupStatic)]
        assert any("Blocked by deny-plop" in t for t in texts)


@pytest.mark.asyncio
async def test_truly_empty_result_is_inert() -> None:
    # No structured output and no output_text: nothing to unfold, so the header is inert.
    app = _ResultApp(_completed_entry(None, ""))
    async with app.run_test() as pilot:
        await pilot.pause()
        result = app.result
        assert result is not None
        section = result.query_one(HeaderCollapsibleSection)
        assert section._collapsible is False
        assert str(section._triangle.render()) == "▪"
