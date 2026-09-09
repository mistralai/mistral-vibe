from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from textual.app import App, ComposeResult

from vibe.app_server.models import (
    CancelledEffectState,
    CompletedEffectState,
    EffectResultDisplay,
    FailedEffectState,
    PublicError,
    SkippedEffectState,
)
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.status_message import IndicatorState
from vibe.cli.textual_ui.widgets.tools import (
    _TOOL_CATEGORY_LABELS,
    ToolGroup,
    ToolGroupHeader,
    _category_label,
    _effect_state_to_indicator,
)
from vibe.utils.tool_presentation import ToolEffectKind

if TYPE_CHECKING:
    pass

_CSS = Path(__file__).parents[3] / "vibe/cli/textual_ui/app.tcss"


class _GroupApp(App[None]):
    CSS_PATH = _CSS

    def __init__(self) -> None:
        super().__init__()
        self.group = ToolGroup()

    def compose(self) -> ComposeResult:
        yield self.group


def test_category_label_covers_all_kinds() -> None:
    for kind in ToolEffectKind:
        assert _category_label(kind)


def test_category_labels_are_plural() -> None:
    assert _TOOL_CATEGORY_LABELS[ToolEffectKind.FILE_READ] == "read files"
    assert _TOOL_CATEGORY_LABELS[ToolEffectKind.SHELL] == "ran commands"
    assert _TOOL_CATEGORY_LABELS[ToolEffectKind.TOOL] == "called tools"


def test_effect_state_to_indicator() -> None:
    completed = CompletedEffectState(
        duration_ms=1.0, display=EffectResultDisplay(success=True, message="ok")
    )
    assert _effect_state_to_indicator(completed) == IndicatorState.SUCCESS

    failed = FailedEffectState(
        error=PublicError(message="boom"),
        duration_ms=1.0,
        display=EffectResultDisplay(success=False, message="err"),
    )
    assert _effect_state_to_indicator(failed) == IndicatorState.ERROR

    skipped = SkippedEffectState(
        reason="nope", display=EffectResultDisplay(success=True, message="skipped")
    )
    assert _effect_state_to_indicator(skipped) == IndicatorState.MUTED

    cancelled = CancelledEffectState(reason="user", duration_ms=1.0)
    assert _effect_state_to_indicator(cancelled) == IndicatorState.MUTED


def test_header_accumulates_categories_in_order() -> None:
    header = ToolGroupHeader()
    header.add_category(ToolEffectKind.FILE_READ)
    header.add_category(ToolEffectKind.SHELL)
    header.add_category(ToolEffectKind.FILE_READ)
    assert header.get_content() == "Reading files, running commands"


def test_header_single_call_uses_plural() -> None:
    header = ToolGroupHeader()
    header.add_category(ToolEffectKind.FILE_READ)
    assert header.get_content() == "Reading files"


def test_header_marks_reasoning() -> None:
    header = ToolGroupHeader()
    header.add_category(ToolEffectKind.FILE_READ)
    header.mark_reasoning()
    assert header.get_content() == "Reading files, thinking"


def test_header_last_state_error_after_success() -> None:
    header = ToolGroupHeader()
    header.settle(IndicatorState.SUCCESS)
    header.settle(IndicatorState.ERROR)
    assert header._last_state == IndicatorState.ERROR


def test_header_last_state_success_after_error() -> None:
    header = ToolGroupHeader()
    header.settle(IndicatorState.ERROR)
    header.settle(IndicatorState.SUCCESS)
    assert header._last_state == IndicatorState.SUCCESS


def test_header_last_state_muted_after_success() -> None:
    header = ToolGroupHeader()
    header.settle(IndicatorState.SUCCESS)
    header.settle(IndicatorState.MUTED)
    assert header._last_state == IndicatorState.MUTED


def test_header_last_state_success_after_muted() -> None:
    header = ToolGroupHeader()
    header.settle(IndicatorState.MUTED)
    header.settle(IndicatorState.SUCCESS)
    assert header._last_state == IndicatorState.SUCCESS


def test_header_unknown_kind_falls_back() -> None:
    header = ToolGroupHeader()
    header.add_category(ToolEffectKind.TOOL)
    assert header.get_content() == "Calling tools"


def test_header_settle_keeps_present_tense() -> None:
    header = ToolGroupHeader()
    header.add_category(ToolEffectKind.FILE_READ)
    header.add_category(ToolEffectKind.SHELL)
    header.mark_reasoning()
    header.settle(IndicatorState.SUCCESS)
    assert header._is_spinning is True
    assert header.get_content() == "Reading files, running commands, thinking"


def test_header_stop_spinning_shows_past_tense() -> None:
    header = ToolGroupHeader()
    header.add_category(ToolEffectKind.FILE_READ)
    header.add_category(ToolEffectKind.SHELL)
    header.mark_reasoning()
    header.settle(IndicatorState.SUCCESS)
    header.stop_spinning()
    assert header._is_spinning is False
    assert header.get_content() == "Read files, ran commands, thought"


def test_header_settled_unknown_kind_falls_back() -> None:
    header = ToolGroupHeader()
    header.add_category(ToolEffectKind.TOOL)
    header.settle(IndicatorState.SUCCESS)
    header.stop_spinning()
    assert header.get_content() == "Called tools"


def test_group_finalizes_with_last_call_status() -> None:
    """If the last call succeeds but an earlier call failed, the group shows success."""
    group = ToolGroup()
    group.settle_indicator(IndicatorState.ERROR)
    group.settle_indicator(IndicatorState.SUCCESS)
    group.finalize()
    assert group.header._last_state == IndicatorState.SUCCESS
    assert group.header._is_spinning is False


def test_group_finalizes_with_last_call_failure() -> None:
    """If the last call fails after an earlier success, the group shows error."""
    group = ToolGroup()
    group.settle_indicator(IndicatorState.SUCCESS)
    group.settle_indicator(IndicatorState.ERROR)
    group.finalize()
    assert group.header._last_state == IndicatorState.ERROR
    assert group.header._is_spinning is False


@pytest.mark.asyncio
async def test_group_compose_mounts_header_and_content() -> None:
    app = _GroupApp()
    async with app.run_test(size=(80, 10)) as pilot:
        await pilot.pause()
        group = app.group
        assert group.header is not None
        assert group.content_container is not None
        assert group.content_container.display is False
        assert group.is_collapsed is True


@pytest.mark.asyncio
async def test_group_set_collapsed_toggles_content() -> None:
    app = _GroupApp()
    async with app.run_test(size=(80, 10)) as pilot:
        await pilot.pause()
        group = app.group
        group.set_collapsed(False)
        assert group.is_collapsed is False
        assert group.content_container.display is True
        group.set_collapsed(True)
        assert group.is_collapsed is True
        assert group.content_container.display is False


@pytest.mark.asyncio
async def test_group_sync_visibility_hides_when_all_content_hidden() -> None:
    app = _GroupApp()
    async with app.run_test(size=(80, 10)) as pilot:
        await pilot.pause()
        group = app.group
        child = NoMarkupStatic("hidden")
        child.display = False
        await group.content_container.mount(child)
        group.sync_visibility()
        assert group.display is False


@pytest.mark.asyncio
async def test_group_sync_visibility_shows_when_content_visible() -> None:
    app = _GroupApp()
    async with app.run_test(size=(80, 10)) as pilot:
        await pilot.pause()
        group = app.group
        child = NoMarkupStatic("visible")
        await group.content_container.mount(child)
        group.sync_visibility()
        assert group.display is True
