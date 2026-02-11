from __future__ import annotations

from collections.abc import Callable

import pytest
from textual.pilot import Pilot
from textual.widgets import Input

from vibe.core.paths.global_paths import GLOBAL_ENV_FILE
from vibe.setup.onboarding import OnboardingApp
from vibe.setup.onboarding.screens.api_key import ApiKeyScreen


async def _wait_for(
    condition: Callable[[], bool],
    pilot: Pilot,
    timeout: float = 5.0,
    interval: float = 0.05,
) -> None:
    elapsed = 0.0
    while not condition():
        await pilot.pause(interval)
        if (elapsed := elapsed + interval) >= timeout:
            msg = "Timed out waiting for condition."
            raise AssertionError(msg)


async def pass_welcome_screen(pilot: Pilot) -> None:
    welcome_screen = pilot.app.get_screen("welcome")
    await _wait_for(
        lambda: not welcome_screen.query_one("#enter-hint").has_class("hidden"), pilot
    )
    await pilot.press("enter")
    await _wait_for(lambda: isinstance(pilot.app.screen, ApiKeyScreen), pilot)


@pytest.mark.asyncio
async def test_ui_gets_through_the_onboarding_successfully() -> None:
    app = OnboardingApp()
    api_key_value = "sk-onboarding-test-key"

    async with app.run_test() as pilot:
        await pass_welcome_screen(pilot)

        api_screen = app.screen
        input_widget = api_screen.query_one("#key", Input)
        await pilot.press(*api_key_value)
        assert input_widget.value == api_key_value

        await pilot.press("enter")
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert app.return_value == "completed"

    assert GLOBAL_ENV_FILE.path.is_file()
    env_contents = GLOBAL_ENV_FILE.path.read_text(encoding="utf-8")
    assert "MISTRAL_API_KEY" in env_contents
    assert api_key_value in env_contents
