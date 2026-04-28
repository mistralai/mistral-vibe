from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config


@pytest.mark.asyncio
async def test_ctrl_y_triggers_copy_selection() -> None:
    """Test that ctrl+y keybinding triggers copy_selection_to_clipboard."""
    app = build_test_vibe_app()

    with patch("vibe.cli.textual_ui.app.copy_selection_to_clipboard") as mock_copy:
        async with app.run_test() as pilot:
            await pilot.press("ctrl+y")
            mock_copy.assert_called_once_with(app, show_toast=False)


@pytest.mark.asyncio
async def test_ctrl_shift_c_triggers_copy_selection() -> None:
    """Test that ctrl+shift+c keybinding triggers copy_selection_to_clipboard."""
    app = build_test_vibe_app()

    with patch("vibe.cli.textual_ui.app.copy_selection_to_clipboard") as mock_copy:
        async with app.run_test() as pilot:
            await pilot.press("ctrl+shift+c")
            mock_copy.assert_called_once_with(app, show_toast=False)


@pytest.mark.asyncio
async def test_mouse_up_respects_autocopy_config_enabled() -> None:
    """Test that mouse up copies when autocopy_to_clipboard is True."""
    app = build_test_vibe_app(config=build_test_vibe_config(autocopy_to_clipboard=True))

    with patch("vibe.cli.textual_ui.app.copy_selection_to_clipboard") as mock_copy:
        async with app.run_test() as pilot:
            await pilot.click()
            mock_copy.assert_called_once_with(app, show_toast=True)


@pytest.mark.asyncio
async def test_mouse_up_respects_autocopy_config_disabled() -> None:
    """Test that mouse up does not copy when autocopy_to_clipboard is False."""
    app = build_test_vibe_app(
        config=build_test_vibe_config(autocopy_to_clipboard=False)
    )

    with patch("vibe.cli.textual_ui.app.copy_selection_to_clipboard") as mock_copy:
        async with app.run_test() as pilot:
            await pilot.click()
            mock_copy.assert_not_called()
