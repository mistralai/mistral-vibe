from __future__ import annotations

import asyncio
from collections.abc import Callable
import time
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.cli.textual_ui.app import BottomApp
from vibe.cli.textual_ui.widgets.theme_picker import ThemePickerApp
from vibe.config_values import AUTO_THEME


async def _wait_until(
    pilot, predicate: Callable[[], bool], timeout: float = 2.0
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await pilot.pause(0.05)
    return False


async def _wait_until_drained(pilot, app, timeout: float = 2.0) -> bool:
    # Config-persisting pickers defer their side effect to the main queue, so
    # callers must wait for the drain (and the pending flag it clears) before
    # asserting on the persisted state. See ADR 0012.
    return await _wait_until(pilot, lambda: not app._queue.draining, timeout)


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
async def test_theme_picker_select_does_not_reload_config() -> None:
    config = build_test_vibe_config(theme="ansi-dark")
    app = build_test_vibe_app(config=config)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        config_resource = app.app_server.resources.config
        with (
            patch.object(
                config_resource, "update", new=AsyncMock(wraps=config_resource.update)
            ) as update_config,
            patch.object(config_resource, "reload", new=AsyncMock()) as reload_config,
        ):
            await app.on_theme_picker_app_theme_selected(
                ThemePickerApp.ThemeSelected("dracula")
            )
            # Theme persistence is deferred to the queue; wait for it to drain.
            assert await _wait_until_drained(pilot, app)

        update_config.assert_awaited_once_with({"theme": "dracula"})
        reload_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_theme_picker_select_applies_before_persisting() -> None:
    config = build_test_vibe_config(theme="ansi-dark")
    app = build_test_vibe_app(config=config)
    update_started = asyncio.Event()
    allow_update = asyncio.Event()

    async def delayed_update(_changes: dict[str, object]) -> None:
        update_started.set()
        await allow_update.wait()

    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        with patch.object(
            app.app_server.resources.config,
            "update",
            new=AsyncMock(side_effect=delayed_update),
        ):
            await app.on_theme_picker_app_theme_selected(
                ThemePickerApp.ThemeSelected("dracula")
            )
            await update_started.wait()

            # The visual theme is applied before the deferred persist runs.
            assert app.theme == "dracula"
            assert app._queue.draining

            allow_update.set()
            assert await _wait_until_drained(pilot, app)
            assert app._pending_theme is None


@pytest.mark.asyncio
async def test_theme_picker_restores_canonical_theme_when_write_fails() -> None:
    config = build_test_vibe_config(theme="ansi-dark")
    app = build_test_vibe_app(config=config)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._apply_theme("dracula")

        with patch.object(
            app.app_server.resources.config,
            "update",
            new=AsyncMock(side_effect=RuntimeError("rejected")),
        ):
            await app.on_theme_picker_app_theme_selected(
                ThemePickerApp.ThemeSelected("dracula")
            )
            # The deferred persist fails and reverts the speculative apply.
            assert await _wait_until_drained(pilot, app)

        assert app.config.theme == "ansi-dark"
        assert app.theme == "ansi-dark"


@pytest.mark.asyncio
async def test_apply_theme_skips_diff_restyle_for_same_render_mode() -> None:
    config = build_test_vibe_config(theme="dracula")
    app = build_test_vibe_app(config=config)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        with patch.object(app, "_restyle_diff_widgets", new=Mock()) as restyle_diffs:
            await app._apply_theme("gruvbox")

        assert app.theme == "gruvbox"
        restyle_diffs.assert_not_called()


@pytest.mark.asyncio
async def test_apply_theme_restyles_diffs_when_render_mode_changes() -> None:
    config = build_test_vibe_config(theme="dracula")
    app = build_test_vibe_app(config=config)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        with patch.object(app, "_restyle_diff_widgets", new=Mock()) as restyle_diffs:
            await app._apply_theme("ansi-dark")

        assert app.theme == "ansi-dark"
        restyle_diffs.assert_called_once_with(ansi=True, dark=True)


@pytest.mark.asyncio
async def test_opening_theme_picker_does_not_restyle_current_theme() -> None:
    config = build_test_vibe_config(theme="dracula")
    app = build_test_vibe_app(config=config)
    async with app.run_test() as pilot:
        await pilot.pause(0.1)

        with patch.object(app, "_restyle_diff_widgets", new=Mock()) as restyle_diffs:
            await app._show_theme()
            await pilot.pause(0.2)

        restyle_diffs.assert_not_called()


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
