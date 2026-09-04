from __future__ import annotations

from pathlib import Path
import tomllib

import pytest
from textual.pilot import Pilot
import tomli_w

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_app,
    build_test_vibe_config,
    wait_until,
)
from vibe.app_server.protocol import ConfigWriteOpWire
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.screens.config import ConfigScreen, ConfigWriteResult
from vibe.cli.textual_ui.screens.config._common import ConfigOptionList
from vibe.cli.textual_ui.screens.config.edit import _TargetedEditScreen
from vibe.cli.textual_ui.widgets.theme_picker import sorted_theme_names
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import ModelConfig, VibeConfigSchema, build_default_orchestrator
from vibe.core.config._defaults import DEFAULT_AUTO_COMPACT_THRESHOLD
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


async def _open_config(app: VibeApp, pilot: Pilot[object]) -> ConfigScreen:
    """Open the settings screen and wait until it is mounted and populated.

    ``ConfigScreen.on_mount`` reads its fields over the app-server protocol
    asynchronously, so the screen can be the current one before its rows exist.
    Polling for populated views instead of pausing a fixed interval keeps the
    test stable under event-loop contention.
    """
    await app._show_config()

    def ready() -> bool:
        screen = app.screen
        return isinstance(screen, ConfigScreen) and bool(screen._views)

    assert await wait_until(pilot, ready)
    screen = app.screen
    assert isinstance(screen, ConfigScreen)
    return screen


async def _filter_to(
    pilot: Pilot[object], screen: ConfigScreen, text: str, name: str
) -> None:
    """Type ``text`` into the filter and wait for ``name`` to be highlighted."""
    for char in text:
        await pilot.press(char)
    assert await wait_until(pilot, lambda: screen._highlighted_name() == name)


async def _open_editor(app: VibeApp, pilot: Pilot[object]) -> None:
    """Press Enter and wait for the edit/choice modal to finish mounting.

    ``ConfigScreen._edit`` runs in a Textual worker that then pushes the modal,
    so the modal is not present the instant Enter is pressed. It is also not
    enough for ``app.screen`` to merely *be* the modal: its (pump-driven)
    ``on_mount`` is what focuses the list and positions the highlight on the
    current value, and callers press ``up``/``down`` immediately afterwards.

    ``on_mount`` is synchronous and sets ``border_title`` before positioning the
    highlight, so a truthy ``border_title`` proves the whole mount step ran.
    """

    def ready() -> bool:
        screen = app.screen
        if not isinstance(screen, _TargetedEditScreen):
            return False
        try:
            content = screen.query_one("#config-edit-content")
        except Exception:
            return False
        return bool(content.border_title)

    await pilot.press("enter")
    assert await wait_until(pilot, ready)


@pytest.mark.asyncio
async def test_config_screen_escape_closes() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        await _open_config(app, pilot)

        await pilot.press("escape")
        assert await wait_until(pilot, lambda: not isinstance(app.screen, ConfigScreen))


@pytest.mark.asyncio
async def test_config_screen_type_to_filter_keeps_a_highlight() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        screen = await _open_config(app, pilot)

        # Lands with the first field highlighted, no search box to focus.
        assert screen._highlighted_name() is not None

        # Typing filters immediately and keeps the first match highlighted.
        for char in "theme":
            await pilot.press(char)
        assert await wait_until(pilot, lambda: screen._query == "theme")
        names = [view.name for view in screen._filtered]
        assert "theme" in names
        assert "models" not in names
        assert screen._highlighted_name() == screen._filtered[0].name

        # Backspace removes from the query.
        await pilot.press("backspace")
        assert await wait_until(pilot, lambda: screen._query == "them")


@pytest.mark.asyncio
async def test_config_screen_splits_popular_and_advanced_sections() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        screen = await _open_config(app, pilot)

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
        screen = await _open_config(app, pilot)

        option_list = screen.query_one("#config-screen-options", ConfigOptionList)
        # First selectable row sits just below the header at index 0.
        assert option_list.highlighted == 1

        # Wrap up to the bottom, forcing the list to scroll down.
        await pilot.press("up")
        assert await wait_until(pilot, lambda: option_list.scroll_offset.y > 0)

        # Wrap back down to the first row; the header must scroll into view.
        await pilot.press("down")
        assert await wait_until(
            pilot,
            lambda: option_list.highlighted == 1 and option_list.scroll_offset.y == 0,
        )


@pytest.mark.asyncio
async def test_config_screen_search_merges_when_few_results() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        screen = await _open_config(app, pilot)

        # A narrow filter yields few hits, so the sections merge into one
        # relevance-ranked list and the strong match leads.
        for char in "redaction":
            await pilot.press(char)
        assert await wait_until(
            pilot,
            lambda: (
                bool(screen._filtered) and screen._filtered[0].name == "otel_redaction"
            ),
        )
        assert None not in screen._rendered_ids  # merged: no section headers


@pytest.mark.asyncio
async def test_config_screen_arrow_down_moves_highlight() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        screen = await _open_config(app, pilot)

        first = screen._highlighted_name()
        await pilot.press("down")
        assert await wait_until(pilot, lambda: screen._highlighted_name() != first)


@pytest.mark.asyncio
async def test_config_screen_toggles_bool_and_persists() -> None:
    app, agent_loop = _app(build_test_vibe_config(autocopy_to_clipboard=False))
    async with app.run_test() as pilot:
        screen = await _open_config(app, pilot)
        await _filter_to(pilot, screen, "autocopy", "autocopy_to_clipboard")
        await _open_editor(app, pilot)  # open the True/False chooser
        await pilot.press("up")  # move from False (current) to True
        await pilot.press("enter")

        assert await wait_until(
            pilot,
            lambda: agent_loop.config_orchestrator.config.autocopy_to_clipboard is True,
        )


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
        screen = await _open_config(app, pilot)
        await _filter_to(pilot, screen, "theme", "theme")

        highlighted = screen._view_by_name(screen._highlighted_name() or "")
        assert highlighted is not None and highlighted.name == "theme"

        # Enter must not open the edit modal for an enforced field.
        await pilot.press("enter")
        assert not await wait_until(
            pilot, lambda: isinstance(app.screen, _TargetedEditScreen), timeout=0.5
        )
        assert isinstance(app.screen, ConfigScreen)

    assert orchestrator.config.theme == "textual-dark"


@pytest.mark.asyncio
async def test_config_screen_single_click_selects_without_editing() -> None:
    app, agent_loop = _app(build_test_vibe_config(autocopy_to_clipboard=False))
    async with app.run_test() as pilot:
        screen = await _open_config(app, pilot)
        await _filter_to(pilot, screen, "autocopy", "autocopy_to_clipboard")

        # Single click highlights the row but must not open the editor/toggle.
        await pilot.click("#config-screen-options", offset=(2, 1), times=1)
        assert not await wait_until(
            pilot, lambda: isinstance(app.screen, _TargetedEditScreen), timeout=0.5
        )
        assert isinstance(app.screen, ConfigScreen)
        assert agent_loop.config_orchestrator.config.autocopy_to_clipboard is False


@pytest.mark.asyncio
async def test_config_screen_enum_edit_via_choice_screen() -> None:
    app, agent_loop = _app(
        build_test_vibe_config(otel_redaction=OtelRedactionMode.DEFAULT)
    )
    async with app.run_test() as pilot:
        screen = await _open_config(app, pilot)
        await _filter_to(pilot, screen, "redaction", "otel_redaction")
        await _open_editor(app, pilot)

        # Choice screen open: move off "default" to "none" and confirm.
        await pilot.press("down")
        await pilot.press("enter")

        assert await wait_until(
            pilot,
            lambda: (
                agent_loop.config_orchestrator.config.otel_redaction
                == OtelRedactionMode.NONE
            ),
        )
        assert isinstance(app.screen, ConfigScreen)


@pytest.mark.asyncio
async def test_config_screen_active_model_uses_choice_picker() -> None:
    app, agent_loop = _app()
    async with app.run_test() as pilot:
        screen = await _open_config(app, pilot)
        orchestrator = agent_loop.config_orchestrator
        models = list(orchestrator.config.models)
        assert len(models) > 1
        original = orchestrator.config.active_model

        await _filter_to(pilot, screen, "active_model", "active_model")
        await _open_editor(app, pilot)

        # Choice screen open with the current model preselected; pick another.
        await pilot.press("down")
        await pilot.press("enter")

        assert await wait_until(
            pilot, lambda: orchestrator.config.active_model != original
        )
        assert isinstance(app.screen, ConfigScreen)
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
        screen = await _open_config(app, pilot)
        await _filter_to(pilot, screen, "active_model", "active_model")
        await _open_editor(app, pilot)

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
        screen = await _open_config(app, pilot)
        await _filter_to(pilot, screen, "active_model", "active_model")
        await _open_editor(app, pilot)

        # Current model "alpha" is preselected (index 1); move up to Default.
        await pilot.press("up")
        await pilot.press("enter")

        assert await wait_until(
            pilot, lambda: agent_loop.config_orchestrator.config.active_model == ""
        )
        assert isinstance(app.screen, ConfigScreen)


@pytest.mark.asyncio
async def test_config_screen_theme_uses_choice_picker() -> None:
    app, agent_loop = _app(build_test_vibe_config(theme="ansi-dark"))
    async with app.run_test() as pilot:
        screen = await _open_config(app, pilot)
        await _filter_to(pilot, screen, "theme", "theme")
        await _open_editor(app, pilot)

        await pilot.press("down")
        await pilot.press("enter")

        assert await wait_until(
            pilot, lambda: agent_loop.config_orchestrator.config.theme != "ansi-dark"
        )
        theme = agent_loop.config_orchestrator.config.theme
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
        config_screen = await _open_config(app, pilot)
        await _filter_to(pilot, config_screen, "theme", "theme")
        await _open_editor(app, pilot)

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
        screen = await _open_config(app, pilot)
        await _filter_to(pilot, screen, "autocopy", "autocopy_to_clipboard")
        await pilot.press("ctrl+r")
        # Removing the override falls back to the schema default (True).
        assert await wait_until(
            pilot, lambda: orchestrator.config.autocopy_to_clipboard is True
        )

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
        screen = await _open_config(app, pilot)
        await _filter_to(pilot, screen, "theme", "theme")
        await _open_editor(app, pilot)
        await pilot.press("down")
        await pilot.press("enter")
        assert await wait_until(pilot, lambda: orchestrator.config.theme != original)

    # The edit persists to the on-disk TOML by default; no session override.
    assert orchestrator.config.theme != original
    disk_after = tomllib.loads(config_file.read_text(encoding="utf-8")).get("theme")
    assert disk_after == orchestrator.config.theme
    overrides = await orchestrator.get_layer("overrides").load()
    assert overrides.model_dump().get("theme") is None


@pytest.mark.asyncio
async def test_config_screen_edits_active_model_compaction_threshold(
    config_dir: Path,
) -> None:
    config_file = config_dir / "config.toml"
    config_file.write_text(
        tomli_w.dumps({
            "active_model": "custom",
            "auto_compact_threshold": 10_000,
            "models": [
                {
                    "name": "custom-model",
                    "provider": "mistral",
                    "alias": "custom",
                    "auto_compact_threshold": 168_000,
                }
            ],
        }),
        encoding="utf-8",
    )
    orchestrator = await build_default_orchestrator()

    app, _ = _app(orchestrator=orchestrator)
    async with app.run_test() as pilot:
        screen = await _open_config(app, pilot)
        view = next(
            view for view in screen._views if view.name == "auto_compact_threshold"
        )
        assert view.value == 168_000
        assert view.path == "/models/custom/auto_compact_threshold"

        await screen._write(
            view,
            [
                ConfigWriteOpWire(
                    op="set", path=view.path, value=10_000, target_layer="user-toml"
                )
            ],
            reason="test active-model compaction threshold",
        )
        assert await wait_until(
            pilot, lambda: orchestrator.config.auto_compact_threshold == 10_000
        )

    assert orchestrator.config.auto_compact_threshold == 10_000
    assert orchestrator.config.get_active_model().auto_compact_threshold == 10_000
    persisted = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert persisted["models"][0]["auto_compact_threshold"] == 10_000


@pytest.mark.asyncio
async def test_config_screen_reset_clears_inherited_compaction_threshold(
    config_dir: Path,
) -> None:
    config_file = config_dir / "config.toml"
    config_file.write_text(
        tomli_w.dumps({
            "active_model": "custom",
            "auto_compact_threshold": 10_000,
            "models": [
                {"name": "custom-model", "provider": "mistral", "alias": "custom"}
            ],
        }),
        encoding="utf-8",
    )
    orchestrator = await build_default_orchestrator()

    app, _ = _app(orchestrator=orchestrator)
    async with app.run_test() as pilot:
        screen = await _open_config(app, pilot)
        view = next(
            view for view in screen._views if view.name == "auto_compact_threshold"
        )
        assert view.path == "/auto_compact_threshold"

        screen._reset(view)
        assert await wait_until(
            pilot,
            lambda: (
                orchestrator.config.get_active_model().auto_compact_threshold
                == DEFAULT_AUTO_COMPACT_THRESHOLD
            ),
        )

    assert (
        orchestrator.config.get_active_model().auto_compact_threshold
        == DEFAULT_AUTO_COMPACT_THRESHOLD
    )
    persisted = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert "auto_compact_threshold" not in persisted


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
        screen = await _open_config(app, pilot)
        await _filter_to(pilot, screen, "theme", "theme")
        await _open_editor(app, pilot)
        # No project config file is discovered here, so targets are just
        # user -> session override: one tab arms the ephemeral layer.
        await pilot.press("tab")
        await pilot.press("down")
        await pilot.press("enter")
        assert await wait_until(pilot, lambda: orchestrator.config.theme != original)

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
        screen = await _open_config(app, pilot)
        await _filter_to(pilot, screen, "theme", "theme")
        await pilot.press("ctrl+r")
        # The reset worker runs and takes the pinned no-op path, which notifies
        # instead of writing; waiting on that keeps the assertion deterministic.
        assert await wait_until(
            pilot, lambda: any("pinned" in n.message for n in app._notifications)
        )

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
        screen = await _open_config(app, pilot)
        await _filter_to(pilot, screen, "theme", "theme")
        await _open_editor(app, pilot)
        await pilot.press("down")
        await pilot.press("enter")
        # Gate on the screen's own view, not just the orchestrator: the config
        # value flips when the write callback returns, but reset reads the
        # screen's layer_values, which only refresh once _sync_views completes.
        assert await wait_until(
            pilot,
            lambda: (
                (view := screen._view_by_name("theme")) is not None
                and view.value != original
            ),
        )
        # The edit persisted to TOML by default; Ctrl+R peels it back.
        await pilot.press("ctrl+r")
        assert await wait_until(pilot, lambda: orchestrator.config.theme == original)

    assert orchestrator.config.theme == original


@pytest.mark.asyncio
async def test_config_screen_deferred_write_informs_user() -> None:
    app = build_test_vibe_app()
    async with app.run_test() as pilot:
        screen = await _open_config(app, pilot)
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

        assert screen._dirty is False
        assert await wait_until(
            pilot,
            lambda: any(
                "will apply when the session is idle" in n.message
                for n in app._notifications
            ),
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
