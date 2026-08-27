from __future__ import annotations

from pathlib import Path
import tomllib

import pytest
import tomli_w

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_app,
    build_test_vibe_config,
)
from vibe.app_server.protocol import ConfigWriteOpWire
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.screens.config import ConfigScreen, ConfigWriteResult
from vibe.cli.textual_ui.screens.config._common import ConfigOptionList
from vibe.cli.textual_ui.screens.config.edit import _TargetedEditScreen
from vibe.cli.textual_ui.widgets.theme_picker import sorted_theme_names
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import ModelConfig, VibeConfigSchema, build_default_orchestrator
from vibe.core.config.layers.admin import AdminConfigLayer
from vibe.core.config.models import OtelRedactionMode
from vibe.core.config.orchestrator import ConfigOrchestrator


def _app(
    config: VibeConfigSchema | None = None,
    *,
    orchestrator: ConfigOrchestrator[VibeConfigSchema] | None = None,
) -> tuple[VibeApp, AgentLoop]:
    """Full app backed by an in-process app server over a chosen orchestrator.

    The config screen is a protocol client, so tests drive it through the real
    app-server config resource; assertions read the orchestrator it wraps.
    """
    agent_loop = build_test_agent_loop(config=config or build_test_vibe_config())
    if orchestrator is not None:
        # tool_manager reads via ``lambda: self.config``, so swapping the
        # orchestrator keeps the whole loop pointed at it.
        agent_loop._config_orchestrator = orchestrator
    return build_test_vibe_app(agent_loop=agent_loop), agent_loop


@pytest.mark.asyncio
async def test_config_screen_escape_closes() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)
        assert isinstance(app.screen, ConfigScreen)

        await pilot.press("escape")
        await pilot.pause(0.2)

        assert not isinstance(app.screen, ConfigScreen)


@pytest.mark.asyncio
async def test_config_screen_type_to_filter_keeps_a_highlight() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)
        screen = app.screen
        assert isinstance(screen, ConfigScreen)

        # Lands with the first field highlighted, no search box to focus.
        assert screen._highlighted_name() is not None

        # Typing filters immediately and keeps the first match highlighted.
        for char in "theme":
            await pilot.press(char)
        await pilot.pause(0.1)
        assert screen._query == "theme"
        names = [view.name for view in screen._filtered]
        assert "theme" in names
        assert "models" not in names
        assert screen._highlighted_name() == screen._filtered[0].name

        # Backspace removes from the query.
        await pilot.press("backspace")
        await pilot.pause(0.1)
        assert screen._query == "them"


@pytest.mark.asyncio
async def test_config_screen_splits_popular_and_advanced_sections() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)
        screen = app.screen
        assert isinstance(screen, ConfigScreen)

        # Both tiers show, popular fields ordered ahead of advanced ones.
        names = [view.name for view in screen._filtered]
        assert "active_model" in names  # popular
        assert "otel_redaction" in names  # advanced
        assert names.index("active_model") < names.index("otel_redaction")

        # Section headers are rendered as non-selectable rows.
        assert None in screen._rendered_ids


@pytest.mark.asyncio
async def test_config_screen_wrap_to_top_keeps_headers_visible() -> None:
    app = build_test_vibe_app()
    # Small viewport so the full list cannot fit and must scroll.
    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)
        screen = app.screen
        assert isinstance(screen, ConfigScreen)

        option_list = screen.query_one("#config-screen-options", ConfigOptionList)
        # First selectable row sits just below the header at index 0.
        assert option_list.highlighted == 1

        # Wrap up to the bottom, forcing the list to scroll down.
        await pilot.press("up")
        await pilot.pause(0.1)
        assert option_list.scroll_offset.y > 0

        # Wrap back down to the first row; the header must scroll into view.
        await pilot.press("down")
        await pilot.pause(0.1)
        assert option_list.highlighted == 1
        assert option_list.scroll_offset.y == 0


@pytest.mark.asyncio
async def test_config_screen_search_merges_when_few_results() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)
        screen = app.screen
        assert isinstance(screen, ConfigScreen)

        # A narrow filter yields few hits, so the sections merge into one
        # relevance-ranked list and the strong match leads.
        for char in "redaction":
            await pilot.press(char)
        await pilot.pause(0.1)
        assert screen._filtered[0].name == "otel_redaction"
        assert None not in screen._rendered_ids  # merged: no section headers


@pytest.mark.asyncio
async def test_config_screen_arrow_down_moves_highlight() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)
        screen = app.screen
        assert isinstance(screen, ConfigScreen)

        first = screen._highlighted_name()
        await pilot.press("down")
        await pilot.pause(0.1)
        assert screen._highlighted_name() != first


@pytest.mark.asyncio
async def test_config_screen_toggles_bool_and_persists() -> None:
    app, agent_loop = _app(build_test_vibe_config(autocopy_to_clipboard=False))
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)

        for char in "autocopy":
            await pilot.press(char)
        await pilot.pause(0.1)
        await pilot.press("enter")  # open the True/False chooser
        await pilot.pause(0.2)
        await pilot.press("up")  # move from False (current) to True
        await pilot.press("enter")
        await pilot.pause(0.3)

        assert agent_loop.config_orchestrator.config.autocopy_to_clipboard is True


async def _orchestrator_with_enforced_theme(
    theme: str,
) -> ConfigOrchestrator[VibeConfigSchema]:
    orchestrator = await build_default_orchestrator()
    admin = next(
        layer for layer in orchestrator.layers if isinstance(layer, AdminConfigLayer)
    )
    admin.load_managed_toml(f'theme = "{theme}"\n')
    await orchestrator.reload()
    return orchestrator


@pytest.mark.asyncio
async def test_config_screen_enforced_field_blocks_edit() -> None:
    orchestrator = await _orchestrator_with_enforced_theme("textual-dark")
    assert orchestrator.config.theme == "textual-dark"

    app, _ = _app(orchestrator=orchestrator)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        await app._show_config()
        await pilot.pause(0.2)
        for char in "theme":
            await pilot.press(char)
        await pilot.pause(0.1)

        screen = app.screen
        assert isinstance(screen, ConfigScreen)
        highlighted = screen._view_by_name(screen._highlighted_name() or "")
        assert highlighted is not None and highlighted.name == "theme"

        # Enter must not open the edit modal for an enforced field.
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert isinstance(app.screen, ConfigScreen)

    assert orchestrator.config.theme == "textual-dark"


@pytest.mark.asyncio
async def test_config_screen_single_click_selects_without_editing() -> None:
    app, agent_loop = _app(build_test_vibe_config(autocopy_to_clipboard=False))
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)

        for char in "autocopy":
            await pilot.press(char)
        await pilot.pause(0.1)

        # Single click highlights the row but must not open the editor/toggle.
        await pilot.click("#config-screen-options", offset=(2, 1), times=1)
        await pilot.pause(0.2)

        assert isinstance(app.screen, ConfigScreen)
        assert agent_loop.config_orchestrator.config.autocopy_to_clipboard is False


@pytest.mark.asyncio
async def test_config_screen_enum_edit_via_choice_screen() -> None:
    app, agent_loop = _app(
        build_test_vibe_config(otel_redaction=OtelRedactionMode.DEFAULT)
    )
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)

        for char in "redaction":
            await pilot.press(char)
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.3)

        # Choice screen open: move off "default" to "none" and confirm.
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.3)

        assert isinstance(app.screen, ConfigScreen)
        assert (
            agent_loop.config_orchestrator.config.otel_redaction
            == OtelRedactionMode.NONE
        )


@pytest.mark.asyncio
async def test_config_screen_active_model_uses_choice_picker() -> None:
    app, agent_loop = _app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)
        orchestrator = agent_loop.config_orchestrator
        models = list(orchestrator.config.models)
        assert len(models) > 1
        original = orchestrator.config.active_model

        for char in "active_model":
            await pilot.press(char)
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.3)

        # Choice screen open with the current model preselected; pick another.
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.3)

        assert isinstance(app.screen, ConfigScreen)
        assert orchestrator.config.active_model != original
        assert orchestrator.config.active_model in models


@pytest.mark.asyncio
async def test_config_screen_active_model_offers_default_option() -> None:
    from textual.widgets import OptionList

    models = [
        ModelConfig(name="model-a", provider="mistral", alias="alpha"),
        ModelConfig(name="model-b", provider="mistral", alias="beta"),
    ]
    app, _ = _app(build_test_vibe_config(models=models, active_model="alpha"))
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)

        for char in "active_model":
            await pilot.press(char)
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.3)

        option_list = app.screen.query_one(OptionList)
        # A leading "Default" option precedes the two configured models.
        assert option_list.option_count == 3
        assert str(option_list.get_option_at_index(0).prompt).startswith(
            "default (currently "
        )


@pytest.mark.asyncio
async def test_config_screen_select_default_unpins_active_model() -> None:
    models = [
        ModelConfig(name="model-a", provider="mistral", alias="alpha"),
        ModelConfig(name="model-b", provider="mistral", alias="beta"),
    ]
    app, agent_loop = _app(build_test_vibe_config(models=models, active_model="alpha"))
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)

        for char in "active_model":
            await pilot.press(char)
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.3)

        # Current model "alpha" is preselected (index 1); move up to Default.
        await pilot.press("up")
        await pilot.press("enter")
        await pilot.pause(0.3)

        assert isinstance(app.screen, ConfigScreen)
        assert agent_loop.config_orchestrator.config.active_model == ""


@pytest.mark.asyncio
async def test_config_screen_theme_uses_choice_picker() -> None:
    app, agent_loop = _app(build_test_vibe_config(theme="ansi-dark"))
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)

        for char in "theme":
            await pilot.press(char)
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.3)

        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.3)

        theme = agent_loop.config_orchestrator.config.theme
        assert theme != "ansi-dark"
        assert theme in sorted_theme_names()


@pytest.mark.asyncio
async def test_config_screen_edit_shows_active_layers(config_dir: Path) -> None:
    config_file = config_dir / "config.toml"
    data = tomllib.loads(config_file.read_text(encoding="utf-8"))
    data["theme"] = "textual-dark"
    config_file.write_text(tomli_w.dumps(data), encoding="utf-8")
    orchestrator = await build_default_orchestrator()

    app, _ = _app(orchestrator=orchestrator)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        await app._show_config()
        await pilot.pause(0.2)
        for char in "theme":
            await pilot.press(char)
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.3)

        screen = app.screen
        assert isinstance(screen, _TargetedEditScreen)
        layers = [(lv.layer, lv.value) for lv in screen._layer_values]
        # Active (writable) layer first, schema default last, and the default
        # row must appear exactly once (the default layer already provides it).
        assert layers == [("user-toml", "textual-dark"), ("default", "auto")]


@pytest.mark.asyncio
async def test_config_screen_reset_removes_user_override(config_dir: Path) -> None:
    config_file = config_dir / "config.toml"
    data = tomllib.loads(config_file.read_text(encoding="utf-8"))
    data["autocopy_to_clipboard"] = False
    config_file.write_text(tomli_w.dumps(data), encoding="utf-8")

    orchestrator = await build_default_orchestrator()
    assert orchestrator.config.autocopy_to_clipboard is False

    app, _ = _app(orchestrator=orchestrator)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        await app._show_config()
        await pilot.pause(0.2)
        for char in "autocopy":
            await pilot.press(char)
        await pilot.pause(0.1)
        await pilot.press("ctrl+r")
        await pilot.pause(0.3)

    # Removing the override falls back to the schema default (True).
    assert orchestrator.config.autocopy_to_clipboard is True


@pytest.mark.asyncio
async def test_config_screen_edit_defaults_to_persisting_to_toml(
    config_dir: Path,
) -> None:
    config_file = config_dir / "config.toml"
    orchestrator = await build_default_orchestrator()
    original = orchestrator.config.theme

    app, _ = _app(orchestrator=orchestrator)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        await app._show_config()
        await pilot.pause(0.2)
        for char in "theme":
            await pilot.press(char)
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.3)
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.3)

    # The edit persists to the on-disk TOML by default; no session override.
    assert orchestrator.config.theme != original
    disk_after = tomllib.loads(config_file.read_text(encoding="utf-8")).get("theme")
    assert disk_after == orchestrator.config.theme
    overrides = await orchestrator.get_layer("overrides").load()
    assert overrides.model_dump().get("theme") is None


@pytest.mark.asyncio
async def test_config_screen_tab_switches_edit_to_session_override(
    config_dir: Path,
) -> None:
    config_file = config_dir / "config.toml"
    orchestrator = await build_default_orchestrator()
    original = orchestrator.config.theme
    disk_before = tomllib.loads(config_file.read_text(encoding="utf-8")).get("theme")

    app, _ = _app(orchestrator=orchestrator)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        await app._show_config()
        await pilot.pause(0.2)
        for char in "theme":
            await pilot.press(char)
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.3)
        # Tab arms the ephemeral session override before confirming.
        await pilot.press("tab")
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.3)

    # The live config reflects the edit, but it lives only in the ephemeral
    # overrides layer; the on-disk TOML is untouched.
    assert orchestrator.config.theme != original
    disk_after = tomllib.loads(config_file.read_text(encoding="utf-8")).get("theme")
    assert disk_after == disk_before
    overrides = await orchestrator.get_layer("overrides").load()
    assert overrides.model_dump().get("theme") == orchestrator.config.theme


@pytest.mark.asyncio
async def test_config_screen_reset_noop_when_env_pins_field(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = config_dir / "config.toml"
    data = tomllib.loads(config_file.read_text(encoding="utf-8"))
    data["theme"] = "textual-dark"
    config_file.write_text(tomli_w.dumps(data), encoding="utf-8")
    monkeypatch.setenv("VIBE_THEME", "ansi-dark")

    orchestrator = await build_default_orchestrator()
    assert orchestrator.config.theme == "ansi-dark"

    app, _ = _app(orchestrator=orchestrator)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        await app._show_config()
        await pilot.pause(0.2)
        for char in "theme":
            await pilot.press(char)
        await pilot.pause(0.1)
        await pilot.press("ctrl+r")
        await pilot.pause(0.3)

    # Env pins the value, so Ctrl+R must not touch the shadowed TOML layer.
    assert orchestrator.config.theme == "ansi-dark"
    disk_after = tomllib.loads(config_file.read_text(encoding="utf-8")).get("theme")
    assert disk_after == "textual-dark"


@pytest.mark.asyncio
async def test_config_screen_reset_clears_persisted_edit(config_dir: Path) -> None:
    orchestrator = await build_default_orchestrator()
    original = orchestrator.config.theme

    app, _ = _app(orchestrator=orchestrator)
    async with app.run_test() as pilot:
        await pilot.pause(0.2)
        await app._show_config()
        await pilot.pause(0.2)
        for char in "theme":
            await pilot.press(char)
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.3)
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert orchestrator.config.theme != original
        # The edit persisted to TOML by default; Ctrl+R peels it back.
        await pilot.press("ctrl+r")
        await pilot.pause(0.3)

    assert orchestrator.config.theme == original


@pytest.mark.asyncio
async def test_config_screen_deferred_write_informs_user() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        await app._show_config()
        await pilot.pause(0.2)
        screen = app.screen
        assert isinstance(screen, ConfigScreen)
        view = screen._view_by_name("autocopy_to_clipboard")
        assert view is not None

        async def deferred(
            _ops: list[ConfigWriteOpWire], _reason: str
        ) -> ConfigWriteResult:
            return ConfigWriteResult.DEFERRED

        screen._write_callback = deferred
        await screen._write(
            view,
            [
                ConfigWriteOpWire(
                    op="set",
                    path=view.path,
                    value=not view.value,
                    target_layer="user-toml",
                )
            ],
            reason="test deferred edit",
        )
        await pilot.pause(0.1)

        assert screen._dirty is False
        assert any(
            "will apply when the session is idle" in n.message
            for n in app._notifications
        )


@pytest.mark.asyncio
async def test_run_config_patch_reloads_ui_after_write() -> None:
    from unittest.mock import AsyncMock, patch

    app, _ = _app(build_test_vibe_config(autocopy_to_clipboard=False))
    async with app.run_test():
        with patch.object(app, "_reload_config", new=AsyncMock()) as reload_config:
            await app._run_config_patch(
                [
                    ConfigWriteOpWire(
                        op="set", path="/autocopy_to_clipboard", value=True
                    )
                ],
                "test deferred write",
            )
            reload_config.assert_awaited_once()
        assert app.app_server.resources.config.current.autocopy_to_clipboard is True
