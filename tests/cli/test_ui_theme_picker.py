from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.cli.textual_ui.app import BottomApp
from vibe.cli.textual_ui.widgets.theme_picker import ThemePickerApp


@pytest.mark.asyncio
async def test_theme_opens_theme_picker() -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_theme()
        await pilot.pause(0.2)

        assert app._current_bottom_app == BottomApp.ThemePicker
        assert len(app.query(ThemePickerApp)) == 1


@pytest.mark.asyncio
async def test_theme_picker_lists_themes_and_marks_current() -> None:
    config = build_test_vibe_config()
    config.theme = "dracula"
    app = build_test_vibe_app(config=config)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_theme()
        await pilot.pause(0.2)

        picker = app.query_one(ThemePickerApp)
        assert "dracula" in picker._theme_names
        assert "ansi-dark" in picker._theme_names
        assert picker._current_theme == "dracula"


@pytest.mark.asyncio
async def test_theme_picker_escape_restores_original_theme() -> None:
    config = build_test_vibe_config()
    config.theme = "ansi-dark"
    app = build_test_vibe_app(config=config)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_theme()
        await pilot.pause(0.2)

        # Move highlight to a different theme to trigger preview.
        await pilot.press("down")
        await pilot.pause(0.2)

        with patch("vibe.cli.textual_ui.app.VibeConfig.save_updates") as mock_save:
            await pilot.press("escape")
            await pilot.pause(0.2)

            mock_save.assert_not_called()

        assert app._current_bottom_app == BottomApp.Input
        assert len(app.query(ThemePickerApp)) == 0
        assert app.config.theme == "ansi-dark"
        assert app.theme == "ansi-dark"


@pytest.mark.asyncio
async def test_theme_picker_select_persists_and_applies() -> None:
    config = build_test_vibe_config()
    config.theme = "ansi-dark"
    app = build_test_vibe_app(config=config)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_theme()
        await pilot.pause(0.2)

        picker = app.query_one(ThemePickerApp)
        names = picker._theme_names
        current_index = names.index("ansi-dark")
        target_index = (current_index + 1) % len(names)
        target = names[target_index]

        await pilot.press("down")

        with patch("vibe.cli.textual_ui.app.VibeConfig.save_updates") as mock_save:
            await pilot.press("enter")
            await pilot.pause(0.2)

            mock_save.assert_called_once_with({"theme": target})

        assert app._current_bottom_app == BottomApp.Input
        assert len(app.query(ThemePickerApp)) == 0
        assert app.config.theme == target
        assert app.theme == target
