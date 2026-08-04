from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.cli.textual_ui.app import BottomApp
from vibe.cli.textual_ui.widgets.theme_picker import ThemePickerApp
from vibe.config_values import AUTO_THEME


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
    config = build_test_vibe_config(theme="dracula")
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
    config = build_test_vibe_config(theme="ansi-dark")
    app = build_test_vibe_app(config=config)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_theme()
        await pilot.pause(0.2)

        # Move highlight to a different theme to trigger preview.
        await pilot.press("down")
        await pilot.pause(0.2)

        with patch.object(
            app.app_server.resources.config, "update", new=AsyncMock()
        ) as update_config:
            await pilot.press("escape")
            await pilot.pause(0.2)

            update_config.assert_not_awaited()

        assert app._current_bottom_app == BottomApp.Input
        assert len(app.query(ThemePickerApp)) == 0
        assert app.config.theme == "ansi-dark"
        assert app.theme == "ansi-dark"


@pytest.mark.asyncio
async def test_theme_picker_select_persists_and_applies() -> None:
    config = build_test_vibe_config(theme="ansi-dark")
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

        config_resource = app.app_server.resources.config
        with patch.object(
            config_resource, "update", new=AsyncMock(wraps=config_resource.update)
        ) as update_config:
            await pilot.press("enter")
            await pilot.pause(0.2)

            update_config.assert_awaited_once_with({"theme": target})

        assert app._current_bottom_app == BottomApp.Input
        assert len(app.query(ThemePickerApp)) == 0
        assert app.config.theme == target
        assert app.theme == target


@pytest.mark.asyncio
async def test_theme_picker_restores_canonical_theme_when_write_fails() -> None:
    config = build_test_vibe_config(theme="ansi-dark")
    app = build_test_vibe_app(config=config)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._apply_theme("dracula")

        with (
            patch.object(
                app.app_server.resources.config,
                "update",
                new=AsyncMock(side_effect=RuntimeError("rejected")),
            ),
            pytest.raises(RuntimeError, match="rejected"),
        ):
            await app.on_theme_picker_app_theme_selected(
                ThemePickerApp.ThemeSelected("dracula")
            )

        assert app.config.theme == "ansi-dark"
        assert app.theme == "ansi-dark"


@pytest.mark.asyncio
async def test_config_theme_change_applies_via_pubsub() -> None:
    config = build_test_vibe_config(theme="ansi-dark")
    app = build_test_vibe_app(config=config)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        assert app.theme == "ansi-dark"

        await app.app_server.resources.config.update({"theme": "dracula"})
        await pilot.pause(0.2)

        assert app.config.theme == "dracula"
        assert app.theme == "dracula"


@pytest.mark.asyncio
async def test_theme_picker_persists_auto_and_applies_resolved_theme() -> None:
    config = build_test_vibe_config(theme="ansi-dark")
    app = build_test_vibe_app(config=config)

    with patch(
        "vibe.cli._theme_detection.resolve_auto_theme", return_value="ansi-light"
    ):
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            await app._show_theme()
            await pilot.pause(0.2)

            picker = app.query_one(ThemePickerApp)
            current_index = picker._theme_names.index(config.theme)
            await pilot.press(*["up"] * current_index)
            await pilot.press("enter")
            await pilot.pause(0.2)

    assert app.config.theme == AUTO_THEME
    assert app.theme == "ansi-light"


@pytest.mark.asyncio
async def test_theme_picker_jk_moves_cursor() -> None:
    from textual.widgets import OptionList

    config = build_test_vibe_config(theme="ansi-dark")
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
