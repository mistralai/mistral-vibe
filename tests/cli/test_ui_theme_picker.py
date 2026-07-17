from __future__ import annotations

from unittest.mock import AsyncMock, patch

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

        orchestrator = app.agent_loop.config_orchestrator
        with patch.object(
            orchestrator, "set_field", new=AsyncMock(return_value=[])
        ) as mock_set_field:
            await pilot.press("escape")
            await pilot.pause(0.2)

            mock_set_field.assert_not_awaited()

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

        orchestrator = app.agent_loop.config_orchestrator
        with patch.object(
            orchestrator, "set_field", new=AsyncMock(wraps=orchestrator.set_field)
        ) as mock_set_field:
            await pilot.press("enter")
            await pilot.pause(0.2)

            mock_set_field.assert_awaited_once_with("/theme", target)

        assert app._current_bottom_app == BottomApp.Input
        assert len(app.query(ThemePickerApp)) == 0
        assert app.config.theme == target
        assert app.theme == target


@pytest.mark.asyncio
async def test_theme_picker_jk_moves_cursor() -> None:
    from textual.widgets import OptionList

    config = build_test_vibe_config()
    config.theme = "ansi-dark"
    app = build_test_vibe_app(config=config)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_theme()
        await pilot.pause(0.2)

        option_list = app.query_one(ThemePickerApp).query_one(OptionList)
        start = option_list.highlighted
        assert start is not None

        await pilot.press("j")
        await pilot.pause(0.1)
        assert option_list.highlighted == (start + 1) % option_list.option_count

        await pilot.press("k")
        await pilot.pause(0.1)
        assert option_list.highlighted == start
