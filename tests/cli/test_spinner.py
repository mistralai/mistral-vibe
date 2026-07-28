from __future__ import annotations

import random

import pytest
from textual.app import App, ComposeResult
from textual.content import Content
from textual.widgets import Static

from vibe.cli.textual_ui.widgets.loading import LoadingWidget
from vibe.cli.textual_ui.widgets.spinner import SpinnerType, create_spinner


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
