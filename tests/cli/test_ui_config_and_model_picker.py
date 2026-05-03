from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.cli.textual_ui.app import BottomApp
from vibe.cli.textual_ui.widgets.config_app import ConfigApp
from vibe.cli.textual_ui.widgets.model_picker import ModelPickerApp
from vibe.cli.textual_ui.widgets.thinking_picker import ThinkingPickerApp
from vibe.core.config._settings import THINKING_LEVELS, ModelConfig


def _make_config_with_models():
    models = [
        ModelConfig(name="model-a", provider="mistral", alias="alpha"),
        ModelConfig(name="model-b", provider="mistral", alias="beta"),
        ModelConfig(name="model-c", provider="mistral", alias="gamma"),
    ]
    return build_test_vibe_config(models=models, active_model="alpha")


# --- /config command ---


@pytest.mark.asyncio
async def test_config_opens_config_app() -> None:
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)

        assert app._current_bottom_app == BottomApp.Config
        assert len(app.query(ConfigApp)) == 1


@pytest.mark.asyncio
async def test_config_escape_returns_to_input() -> None:
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)

        await pilot.press("escape")
        await pilot.pause(0.2)

        assert app._current_bottom_app == BottomApp.Input
        assert len(app.query(ConfigApp)) == 0


@pytest.mark.asyncio
async def test_config_toggle_autocopy() -> None:
    config = _make_config_with_models()
    config.autocopy_to_clipboard = False
    app = build_test_vibe_app(config=config)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)

        # Navigate down to Auto-copy (third item, after Model + Thinking) and toggle
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.1)

        # Verify the toggle happened in the widget
        config_app = app.query_one(ConfigApp)
        assert config_app.changes.get("autocopy_to_clipboard") == "On"


@pytest.mark.asyncio
async def test_config_escape_saves_changes() -> None:
    config = _make_config_with_models()
    config.autocopy_to_clipboard = False
    app = build_test_vibe_app(config=config)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)

        # Toggle auto-copy (skip Model + Thinking rows)
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.1)

        with patch("vibe.cli.textual_ui.app.VibeConfig.save_updates") as mock_save:
            await pilot.press("escape")
            await pilot.pause(0.2)

            mock_save.assert_called_once()
            changes = mock_save.call_args[0][0]
            assert changes["autocopy_to_clipboard"] is True


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

        with patch("vibe.cli.textual_ui.app.VibeConfig.save_updates") as mock_save:
            await pilot.press("escape")
            await pilot.pause(0.2)

            mock_save.assert_not_called()


@pytest.mark.asyncio
async def test_model_picker_select_model() -> None:
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_model()
        await pilot.pause(0.2)

        # Navigate down to "beta" and select
        await pilot.press("down")
        with patch("vibe.cli.textual_ui.app.VibeConfig.save_updates") as mock_save:
            await pilot.press("enter")
            await pilot.pause(0.2)

            mock_save.assert_called_once_with({"active_model": "beta"})

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

        with patch("vibe.cli.textual_ui.app.VibeConfig.save_updates") as mock_save:
            await pilot.press("enter")
            await pilot.pause(0.2)

            mock_save.assert_called_once_with({"active_model": "alpha"})

        assert app._current_bottom_app == BottomApp.Input


# --- config -> model picker flow ---


@pytest.mark.asyncio
async def test_config_model_entry_opens_model_picker() -> None:
    """Pressing Enter on the Model row in /config opens the model picker."""
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)

        # Model row is the first item, already highlighted. Press enter.
        await pilot.press("enter")
        await pilot.pause(0.3)

        assert app._current_bottom_app == BottomApp.ModelPicker
        assert len(app.query(ModelPickerApp)) == 1
        assert len(app.query(ConfigApp)) == 0


@pytest.mark.asyncio
async def test_config_to_model_picker_escape_returns_to_input() -> None:
    """Opening model picker from config, then ESC, returns to input (not config)."""
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)

        # Open model picker from config
        await pilot.press("enter")
        await pilot.pause(0.3)

        # Escape model picker
        await pilot.press("escape")
        await pilot.pause(0.2)

        assert app._current_bottom_app == BottomApp.Input
        assert len(app.query(ModelPickerApp)) == 0
        assert len(app.query(ConfigApp)) == 0


@pytest.mark.asyncio
async def test_config_to_model_picker_select_returns_to_input() -> None:
    """Opening model picker from config, selecting a model, returns to input."""
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)

        # Open model picker from config
        await pilot.press("enter")
        await pilot.pause(0.3)

        # Select second model
        await pilot.press("down")
        with patch("vibe.cli.textual_ui.app.VibeConfig.save_updates") as mock_save:
            await pilot.press("enter")
            await pilot.pause(0.2)

            mock_save.assert_called_once_with({"active_model": "beta"})

        assert app._current_bottom_app == BottomApp.Input


@pytest.mark.asyncio
async def test_config_pending_changes_saved_before_model_picker() -> None:
    """Toggle changes in config are saved before switching to model picker."""
    config = _make_config_with_models()
    config.autocopy_to_clipboard = False
    app = build_test_vibe_app(config=config)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)

        # Toggle auto-copy (third row, after Model + Thinking)
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.1)

        # Go back up to model row and open model picker
        await pilot.press("up")
        await pilot.press("up")
        with patch("vibe.cli.textual_ui.app.VibeConfig.save_updates") as mock_save:
            await pilot.press("enter")
            await pilot.pause(0.3)

            mock_save.assert_called_once()
            changes = mock_save.call_args[0][0]
            assert changes["autocopy_to_clipboard"] is True


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
        with patch.object(app, "_reload_config", new=AsyncMock()):
            await pilot.press("enter")
            await pilot.pause(0.2)

        assert app._current_bottom_app == BottomApp.Input
        assert len(app.query(ThinkingPickerApp)) == 0
        assert app.config.get_active_model().thinking == "low"


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
        with patch.object(app, "_reload_config", new=AsyncMock()):
            await pilot.press("enter")
            await pilot.pause(0.2)

        assert app.config.get_active_model().thinking == "high"


# --- config -> thinking picker flow ---


@pytest.mark.asyncio
async def test_config_thinking_entry_opens_thinking_picker() -> None:
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)

        # Thinking row is the second item (after Model). Navigate down and press enter.
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.3)

        assert app._current_bottom_app == BottomApp.ThinkingPicker
        assert len(app.query(ThinkingPickerApp)) == 1
        assert len(app.query(ConfigApp)) == 0


@pytest.mark.asyncio
async def test_config_to_thinking_picker_escape_returns_to_input() -> None:
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)

        # Open thinking picker from config
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.3)

        # Escape thinking picker
        await pilot.press("escape")
        await pilot.pause(0.2)

        assert app._current_bottom_app == BottomApp.Input
        assert len(app.query(ThinkingPickerApp)) == 0
        assert len(app.query(ConfigApp)) == 0


@pytest.mark.asyncio
async def test_config_to_thinking_picker_select_returns_to_input() -> None:
    app = build_test_vibe_app(config=_make_config_with_models())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)

        # Open thinking picker from config
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.3)

        # Select "medium" (3rd item = 2 downs from "off")
        await pilot.press("down")
        await pilot.press("down")
        with patch.object(app, "_reload_config", new=AsyncMock()):
            await pilot.press("enter")
            await pilot.pause(0.2)

        assert app._current_bottom_app == BottomApp.Input
        assert app.config.get_active_model().thinking == "medium"
