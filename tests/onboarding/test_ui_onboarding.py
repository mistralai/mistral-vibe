from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import tomllib

import pytest
from textual.events import Resize
from textual.geometry import Size
from textual.pilot import Pilot
from textual.widgets import Input

from vibe.core.paths.global_paths import GLOBAL_CONFIG_FILE, GLOBAL_ENV_FILE
from vibe.setup.onboarding import OnboardingApp
from vibe.setup.onboarding.screens.api_key import ApiKeyScreen
from vibe.setup.onboarding.screens.provider_selection import ProviderSelectionScreen
from vibe.setup.onboarding.screens.theme_selection import THEMES, ThemeSelectionScreen


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
    await _wait_for(lambda: isinstance(pilot.app.screen, ThemeSelectionScreen), pilot)


@pytest.mark.asyncio
async def test_ui_gets_through_the_onboarding_successfully() -> None:
    app = OnboardingApp()
    api_key_value = "sk-onboarding-test-key"

    async with app.run_test() as pilot:
        await pass_welcome_screen(pilot)

        # Theme selection -> Provider selection
        await pilot.press("enter")
        await _wait_for(lambda: isinstance(app.screen, ProviderSelectionScreen), pilot)

        # Provider selection -> API key (default is Mistral, button is focused)
        # Small pause to ensure button is focused and screen is ready
        await pilot.pause(0.2)
        # Click on the focused button (Mistral is focused by default)
        await pilot.click("#provider-mistral")
        await _wait_for(lambda: isinstance(app.screen, ApiKeyScreen), pilot, timeout=5.0)

        api_screen = app.screen
        input_widget = api_screen.query_one("#key", Input)
        await pilot.press(*api_key_value)
        assert input_widget.value == api_key_value

        await pilot.press("enter")
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=3.0)

    assert app.return_value == "completed"

    assert GLOBAL_ENV_FILE.path.is_file()
    env_contents = GLOBAL_ENV_FILE.path.read_text(encoding="utf-8")
    assert "MISTRAL_API_KEY" in env_contents
    assert api_key_value in env_contents

    assert GLOBAL_CONFIG_FILE.path.is_file()
    config_contents = GLOBAL_CONFIG_FILE.path.read_text(encoding="utf-8")
    config_dict = tomllib.loads(config_contents)
    assert config_dict.get("textual_theme") == app.theme


@pytest.mark.asyncio
async def test_ui_can_pick_a_theme_and_saves_selection(config_dir: Path) -> None:
    app = OnboardingApp()

    async with app.run_test() as pilot:
        await pass_welcome_screen(pilot)

        theme_screen = app.screen
        assert isinstance(theme_screen, ThemeSelectionScreen)
        app.post_message(
            Resize(Size(40, 10), Size(40, 10))
        )  # trigger the resize event handler
        preview = theme_screen.query_one("#preview")
        assert preview.styles.max_height is not None
        target_theme = "gruvbox"
        # Use the screen's available themes which accounts for terminal theme availability
        available_themes = theme_screen._available_themes
        assert target_theme in available_themes
        start_index = theme_screen._theme_index
        target_index = available_themes.index(target_theme)
        steps_down = (target_index - start_index) % len(available_themes)
        await pilot.press(*["down"] * steps_down)
        assert app.theme == target_theme
        await pilot.press("enter")
        # Theme selection now goes to provider selection, not API key
        await _wait_for(lambda: isinstance(app.screen, ProviderSelectionScreen), pilot)

    assert GLOBAL_CONFIG_FILE.path.is_file()
    config_contents = GLOBAL_CONFIG_FILE.path.read_text(encoding="utf-8")
    config_dict = tomllib.loads(config_contents)
    assert config_dict.get("textual_theme") == target_theme
