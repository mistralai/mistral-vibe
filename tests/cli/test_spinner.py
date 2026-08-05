from __future__ import annotations

import random
from typing import Any

import pytest
from textual.app import App, ComposeResult
from textual.content import Content
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.loading import LoadingWidget
from vibe.cli.textual_ui.widgets.spinner import (
    BrailleSpinner,
    SpinnerType,
    create_spinner,
)
from vibe.cli.textual_ui.widgets.spinner_text import SpinnerText


class _LoadingApp(App[None]):
    CSS = """
    LoadingWidget, .loading-container, .loading-indicator, .loading-status {
        width: auto;
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield LoadingWidget(status="Initializing", show_hint=False)


def test_generate_100_frames_no_crash() -> None:
    """Generate 100 frames per spinner type with seeded random for determinism."""
    random.seed(42)
    for spinner_type in SpinnerType:
        spinner = create_spinner(spinner_type)
        for _ in range(100):
            frame = spinner.next_frame()
            assert isinstance(frame, str)
            assert len(frame) > 0


@pytest.mark.asyncio
async def test_loading_initial_status_is_present_during_first_layout() -> None:
    app = _LoadingApp()

    async with app.run_test() as pilot:
        await pilot.pause()

        status = app.query_one(".loading-status", Static)
        content = status.content
        assert isinstance(content, str)
        assert Content.from_markup(content).plain == "Initializing… "
        assert status.size.width > 0


class _SpinnerTextApp(App[None]):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__()
        self._slot = SpinnerText(id="slot", **kwargs)

    def compose(self) -> ComposeResult:
        yield self._slot


@pytest.mark.asyncio
async def test_spinner_text_animates_while_pending_then_resolves() -> None:
    app = _SpinnerTextApp()
    async with app.run_test() as pilot:
        slot = app._slot
        slot.set_pending(True)
        await pilot.pause()

        assert str(slot.content) in BrailleSpinner.FRAMES
        assert slot._timer is not None

        slot.set_pending(False, resolved="target-testing-model-alias")
        await pilot.pause()

        assert str(slot.content) == "target-testing-model-alias"
        assert slot._timer is None


@pytest.mark.asyncio
async def test_spinner_text_stops_timer_on_unmount() -> None:
    app = _SpinnerTextApp()
    async with app.run_test() as pilot:
        slot = app._slot
        slot.set_pending(True)
        await pilot.pause()
        assert slot._timer is not None

        await slot.remove()
        assert slot._timer is None
