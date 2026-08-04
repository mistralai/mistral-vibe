from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.app_server.config import THINKING_LEVELS
from vibe.cli.textual_ui.app import BottomApp
from vibe.cli.textual_ui.widgets.model_picker import ModelPickerApp
from vibe.cli.textual_ui.widgets.thinking_picker import ThinkingPickerApp
from vibe.core.config import ModelConfig


def _make_config_with_models(**kwargs):
    models = [
        ModelConfig(name="model-a", provider="mistral", alias="alpha"),
        ModelConfig(name="model-b", provider="mistral", alias="beta"),
        ModelConfig(name="model-c", provider="mistral", alias="gamma"),
    ]
    return build_test_vibe_config(models=models, active_model="alpha", **kwargs)


# --- /model command ---


@pytest.mark.asyncio
async def test_model_opens_model_picker() -> None:
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_model()
        await pilot.pause(0.2)

        assert app._current_bottom_app == BottomApp.ModelPicker
        assert len(app.query(ModelPickerApp)) == 1


@pytest.mark.asyncio
async def test_model_picker_shows_all_models() -> None:
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_model()
        await pilot.pause(0.2)

        picker = app.query_one(ModelPickerApp)
        assert picker._model_aliases == ["alpha", "beta", "gamma"]
        assert picker._current_model == "alpha"


@pytest.mark.asyncio
async def test_model_picker_escape_returns_to_input() -> None:
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_model()
        await pilot.pause(0.2)

        await pilot.press("escape")
        await pilot.pause(0.2)

        assert app._current_bottom_app == BottomApp.Input
        assert len(app.query(ModelPickerApp)) == 0


@pytest.mark.asyncio
async def test_model_picker_escape_does_not_save() -> None:
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_model()
        await pilot.pause(0.2)

        with patch.object(
            app.app_server.resources.config, "update", new=AsyncMock()
        ) as update_config:
            await pilot.press("escape")
            await pilot.pause(0.2)

            update_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_model_picker_select_model() -> None:
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_model()
        await pilot.pause(0.2)

        # Navigate down to "beta" and select
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert app.config.active_model.alias == "beta"
        assert app._current_bottom_app == BottomApp.Input
        assert len(app.query(ModelPickerApp)) == 0


@pytest.mark.asyncio
async def test_model_picker_select_current_model() -> None:
    """Selecting the already-active model still saves (idempotent)."""
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_model()
        await pilot.pause(0.2)

        await pilot.press("enter")
        await pilot.pause(0.2)

        assert app.config.active_model.alias == "alpha"
        assert app._current_bottom_app == BottomApp.Input


# --- /thinking command ---


@pytest.mark.asyncio
async def test_thinking_opens_thinking_picker() -> None:
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_thinking()
        await pilot.pause(0.2)

        assert app._current_bottom_app == BottomApp.ThinkingPicker
        assert len(app.query(ThinkingPickerApp)) == 1


@pytest.mark.asyncio
async def test_thinking_picker_shows_all_levels() -> None:
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_thinking()
        await pilot.pause(0.2)

        picker = app.query_one(ThinkingPickerApp)
        assert picker._thinking_levels == THINKING_LEVELS
        assert picker._current_thinking == "off"


@pytest.mark.asyncio
async def test_thinking_picker_escape_returns_to_input() -> None:
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_thinking()
        await pilot.pause(0.2)

        await pilot.press("escape")
        await pilot.pause(0.2)

        assert app._current_bottom_app == BottomApp.Input
        assert len(app.query(ThinkingPickerApp)) == 0


@pytest.mark.asyncio
async def test_thinking_picker_select_level() -> None:
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_thinking()
        await pilot.pause(0.2)

        # Navigate down to "low" (second item) and select
        await pilot.press("down")
        with (
            patch.object(app, "_reload_config", new=AsyncMock()),
            patch.object(
                app.app_server.resources.config, "set_thinking", new=AsyncMock()
            ) as set_thinking,
        ):
            await pilot.press("enter")
            await pilot.pause(0.2)

        assert app._current_bottom_app == BottomApp.Input
        assert len(app.query(ThinkingPickerApp)) == 0
        set_thinking.assert_awaited_once_with("low")


@pytest.mark.asyncio
async def test_thinking_picker_select_high() -> None:
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_thinking()
        await pilot.pause(0.2)

        # Navigate to "high" (4th item = 3 downs from "off")
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("down")
        with (
            patch.object(app, "_reload_config", new=AsyncMock()),
            patch.object(
                app.app_server.resources.config, "set_thinking", new=AsyncMock()
            ) as set_thinking,
        ):
            await pilot.press("enter")
            await pilot.pause(0.2)

        set_thinking.assert_awaited_once_with("high")
