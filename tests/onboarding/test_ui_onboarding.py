from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from textual.events import Resize
from textual.geometry import Size
from textual.pilot import Pilot
from textual.widgets import Input

from vibe.core import config as core_config
from vibe.setup.onboarding import OnboardingApp
import vibe.setup.onboarding.screens.api_key as api_key_module
from vibe.setup.onboarding.screens.api_key import ApiKeyScreen
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


@pytest.fixture()
def onboarding_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[OnboardingApp, Path, dict[str, Any]]:
    vibe_home = tmp_path / ".vibe"
    env_file = vibe_home / ".env"
    saved_updates: dict[str, Any] = {}

    def record_updates(updates: dict[str, Any]) -> None:
        saved_updates.update(updates)

    monkeypatch.setenv("VIBE_HOME", str(vibe_home))

    for module in (core_config, api_key_module):
        monkeypatch.setattr(module, "GLOBAL_CONFIG_DIR", vibe_home, raising=False)
        monkeypatch.setattr(module, "GLOBAL_ENV_FILE", env_file, raising=False)

    monkeypatch.setattr(
        core_config.VibeConfig,
        "save_updates",
        classmethod(lambda cls, updates: record_updates(updates)),
    )

    return OnboardingApp(), env_file, saved_updates


async def pass_welcome_screen(pilot: Pilot) -> None:
    welcome_screen = pilot.app.get_screen("welcome")
    await _wait_for(
        lambda: not welcome_screen.query_one("#enter-hint").has_class("hidden"), pilot
    )
    await pilot.press("enter")
    await _wait_for(lambda: isinstance(pilot.app.screen, ThemeSelectionScreen), pilot)


@pytest.mark.asyncio
async def test_ui_gets_through_the_onboarding_successfully(
    onboarding_app: tuple[OnboardingApp, Path, dict[str, Any]],
) -> None:
    app, env_file, config_updates = onboarding_app
    api_key_value = "sk-onboarding-test-key"

    async with app.run_test() as pilot:
        await pass_welcome_screen(pilot)

        await pilot.press("enter")
        await _wait_for(lambda: isinstance(app.screen, ApiKeyScreen), pilot)
        api_screen = app.screen
        input_widget = api_screen.query_one("#key", Input)
        await pilot.press(*api_key_value)
        assert input_widget.value == api_key_value

        await pilot.press("enter")
        await _wait_for(lambda: app.return_value is not None, pilot, timeout=2.0)

    assert app.return_value == "completed"

    assert env_file.is_file()
    env_contents = env_file.read_text(encoding="utf-8")
    assert "MISTRAL_API_KEY" in env_contents
    assert api_key_value in env_contents

    assert config_updates.get("textual_theme") == app.theme


@pytest.mark.asyncio
async def test_ui_can_pick_a_theme_and_saves_selection(
    onboarding_app: tuple[OnboardingApp, Path, dict[str, Any]],
) -> None:
    app, _, config_updates = onboarding_app

    async with app.run_test() as pilot:
        await pass_welcome_screen(pilot)

        theme_screen = app.screen
        app.post_message(
            Resize(Size(40, 10), Size(40, 10))
        )  # trigger the resize event handler
        preview = theme_screen.query_one("#preview")
        assert preview.styles.max_height is not None
        target_theme = "gruvbox"
        assert target_theme in THEMES
        start_index = THEMES.index(app.theme)
        target_index = THEMES.index(target_theme)
        steps_down = (target_index - start_index) % len(THEMES)
        await pilot.press(*["down"] * steps_down)
        assert app.theme == target_theme
        await pilot.press("enter")
        await _wait_for(lambda: isinstance(app.screen, ApiKeyScreen), pilot)

    assert config_updates.get("textual_theme") == target_theme
