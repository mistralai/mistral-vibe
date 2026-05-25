from __future__ import annotations

import pytest
from textual.pilot import Pilot

from tests.snapshots.snap_compare import SnapCompare
from vibe.setup.onboarding import OnboardingApp
from vibe.setup.onboarding.screens.theme_selection import ThemeSelectionScreen


async def _advance_to_theme_screen(pilot: Pilot) -> None:
    welcome = pilot.app.get_screen("welcome")
    for _ in range(40):
        await pilot.pause(0.1)
        if not welcome.query_one("#enter-hint").has_class("hidden"):
            break
    await pilot.press("enter")
    for _ in range(20):
        if isinstance(pilot.app.screen, ThemeSelectionScreen):
            return
        await pilot.pause(0.1)


@pytest.mark.parametrize("theme", ["ansi-dark", "ansi-light", "dracula"])
def test_snapshot_onboarding_theme_selection(
    snap_compare: SnapCompare, theme: str
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await _advance_to_theme_screen(pilot)
        pilot.app.theme = theme
        await pilot.pause(0.2)

    assert snap_compare(OnboardingApp(), terminal_size=(80, 30), run_before=run_before)
