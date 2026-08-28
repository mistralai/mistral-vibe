from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from textual.widgets import OptionList

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.app_server.models import (
    PluginCatalogDropped,
    PluginCatalogEntry,
    PluginCatalogState,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    PluginCatalogReadResponse,
    ProtocolError,
    ProtocolErrorCode,
)
from vibe.app_server.resources import PluginCatalogChange, PluginCatalogDiff
from vibe.cli.textual_ui.app import BottomApp, VibeApp
from vibe.cli.textual_ui.widgets.messages import ErrorMessage, UserCommandMessage
from vibe.cli.textual_ui.widgets.plugins_app import PluginsApp


def _entry(name: str, digest: str) -> PluginCatalogEntry:
    return PluginCatalogEntry(
        name=name,
        version="1.0.0",
        source_format="agent_plugins_1_0",
        manifest_digest=f"manifest-{name}",
        content_sha256=digest,
    )


def _catalog(*entries: PluginCatalogEntry) -> PluginCatalogState:
    return PluginCatalogState(plugins=list(entries))


async def _prepared_app() -> VibeApp:
    app = build_test_vibe_app(config=build_test_vibe_config())
    await app.prepare()
    return app


def _capture_mounted(app: VibeApp, monkeypatch: pytest.MonkeyPatch) -> list[object]:
    mounted: list[object] = []

    async def mount_and_scroll(widget: object, after: object | None = None) -> None:
        mounted.append(widget)

    monkeypatch.setattr(app, "_mount_and_scroll", mount_and_scroll)
    return mounted


def _capture_switched(app: VibeApp, monkeypatch: pytest.MonkeyPatch) -> list[object]:
    switched: list[object] = []

    async def switch_from_input(widget: object, scroll: bool = False) -> None:
        switched.append(widget)

    monkeypatch.setattr(app, "_switch_from_input", switch_from_input)
    return switched


def _messages(mounted: list[object]) -> str:
    return "\n".join(
        widget._content for widget in mounted if isinstance(widget, UserCommandMessage)
    )


class _CatalogClient:
    """A backend that resolves the given plugins, or none at all when `None`."""

    def __init__(self, state: PluginCatalogState | None) -> None:
        self._state = state

    async def request(self, method: str, params: object) -> dict[str, object]:
        assert method == "plugin_catalog/read"
        if self._state is None:
            raise AppServerResponseError(
                ProtocolError(
                    code=ProtocolErrorCode.NOT_IMPLEMENTED,
                    message="The selected session backend resolves no plugins",
                )
            )
        return PluginCatalogReadResponse(plugins=self._state).model_dump(
            mode="json", by_alias=True
        )


class _CatalogConnection:
    def __init__(self, client: _CatalogClient) -> None:
        self._client = client

    async def connect(self) -> _CatalogClient:
        return self._client


def _serve_catalog(
    app: VibeApp, monkeypatch: pytest.MonkeyPatch, state: PluginCatalogState | None
) -> None:
    """Answer reads the way a backend that does — or does not — resolve plugins."""
    monkeypatch.setattr(
        app.app_server.resources.plugins,
        "_connection",
        _CatalogConnection(_CatalogClient(state)),
    )


# Unskip with the registry entries: both commands are unregistered for this
# release, so the gate this asserts cannot be observed either way.
@pytest.mark.skip(reason="The plugin commands are withheld from the registry")
@pytest.mark.asyncio
async def test_the_commands_are_offered_only_where_a_backend_resolves_plugins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = await _prepared_app()
    _serve_catalog(app, monkeypatch, None)

    await app._probe_plugin_catalog()
    app._refresh_command_registry()
    assert app.commands.get_command_name("/plugins") is None
    assert app.commands.get_command_name("/reload-plugins") is None

    _serve_catalog(app, monkeypatch, _catalog(_entry("productivity", "0bbb23a0")))
    await app._probe_plugin_catalog()
    app._refresh_command_registry()

    assert app.commands.get_command_name("/plugins") == "plugins"
    assert app.commands.get_command_name("/reload-plugins") == "reload-plugins"


@pytest.mark.asyncio
async def test_the_startup_probe_decides_availability_without_a_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `--experimental-harness` is argparse-only and never reaches a client, so
    # the answer to a read is the only thing that can gate the commands.
    app = await _prepared_app()
    _serve_catalog(app, monkeypatch, _catalog(_entry("productivity", "0bbb23a0")))

    await app._probe_plugin_catalog()

    assert app._command_context().plugins_enabled is True


@pytest.mark.asyncio
async def test_a_probe_that_raises_leaves_the_commands_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = await _prepared_app()
    monkeypatch.setattr(
        app.app_server.resources.plugins,
        "read",
        AsyncMock(side_effect=RuntimeError("transport closed")),
    )

    await app._probe_plugin_catalog()

    assert app._command_context().plugins_enabled is False


@pytest.mark.asyncio
async def test_showing_plugins_opens_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    app = await _prepared_app()
    _capture_mounted(app, monkeypatch)
    switched = _capture_switched(app, monkeypatch)
    _serve_catalog(app, monkeypatch, _catalog(_entry("productivity", "0bbb23a0")))

    await app._show_plugins()

    assert [type(widget) for widget in switched] == [PluginsApp]


@pytest.mark.asyncio
async def test_a_session_with_nothing_to_list_says_so_rather_than_opening_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = await _prepared_app()
    mounted = _capture_mounted(app, monkeypatch)
    switched = _capture_switched(app, monkeypatch)
    _serve_catalog(app, monkeypatch, _catalog())

    await app._show_plugins()

    assert switched == []
    assert "No plugins are installed" in _messages(mounted)


@pytest.mark.asyncio
async def test_a_dropped_plugin_alone_is_still_worth_opening_the_list_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = await _prepared_app()
    _capture_mounted(app, monkeypatch)
    switched = _capture_switched(app, monkeypatch)
    _serve_catalog(
        app,
        monkeypatch,
        PluginCatalogState(
            dropped=[
                PluginCatalogDropped(
                    file=".vibe/plugins/broken/plugin.json",
                    message="plugin name must be lowercase",
                )
            ]
        ),
    )

    await app._show_plugins()

    assert [type(widget) for widget in switched] == [PluginsApp]


@pytest.mark.asyncio
async def test_reloading_plugins_reports_what_moved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = await _prepared_app()
    mounted = _capture_mounted(app, monkeypatch)
    after = _catalog(_entry("productivity", "4c71ea55"), _entry("lean", "aa10bb20"))
    monkeypatch.setattr(
        app.app_server.resources.plugins,
        "reload",
        AsyncMock(
            return_value=PluginCatalogDiff(
                changes=(
                    PluginCatalogChange("frontend", "1e040938", None),
                    PluginCatalogChange("lean", None, "aa10bb20"),
                    PluginCatalogChange("productivity", "0bbb23a0", "4c71ea55"),
                ),
                state=after,
            )
        ),
    )

    await app._reload_plugins()

    report = _messages(mounted)
    assert "no longer installed" in report
    assert "0bbb23a0 → 4c71ea55" in report
    assert "lean" in report and "1.0.0" in report


@pytest.mark.asyncio
async def test_a_reload_that_moves_nothing_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = await _prepared_app()
    mounted = _capture_mounted(app, monkeypatch)
    monkeypatch.setattr(
        app.app_server.resources.plugins,
        "reload",
        AsyncMock(
            return_value=PluginCatalogDiff(changes=(), state=PluginCatalogState())
        ),
    )

    await app._reload_plugins()

    # An empty list would read as a reload that did not run.
    assert "Nothing changed" in _messages(mounted)


@pytest.mark.asyncio
async def test_a_failed_reload_surfaces_the_error_and_leaves_the_list_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = await _prepared_app()
    mounted = _capture_mounted(app, monkeypatch)
    monkeypatch.setattr(
        app.app_server.resources.plugins,
        "reload",
        AsyncMock(side_effect=RuntimeError("rescan failed")),
    )

    assert await app._reload_plugins() is None
    assert any(
        isinstance(widget, ErrorMessage) and "rescan failed" in str(widget._error)
        for widget in mounted
    )


@pytest.mark.asyncio
async def test_the_list_mounts_and_escape_hands_the_prompt_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        _serve_catalog(app, monkeypatch, _catalog(_entry("productivity", "0bbb23a0")))
        await app._show_plugins()
        await pilot.pause(0.2)

        assert app._current_bottom_app == BottomApp.Plugins
        assert app.query_one(PluginsApp)._state.plugins[0].name == "productivity"

        await pilot.press("escape")
        await pilot.pause(0.2)

        assert app._current_bottom_app == BottomApp.Input
        assert len(app.query(PluginsApp)) == 0


@pytest.mark.asyncio
async def test_slash_opens_the_filter_and_typing_narrows_the_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        _serve_catalog(
            app,
            monkeypatch,
            _catalog(_entry("productivity", "0bbb23a0"), _entry("lean", "aa10bb20")),
        )
        await app._show_plugins()
        await pilot.pause(0.2)

        await pilot.press("slash")
        await pilot.pause(0.1)
        for key in "lean":
            await pilot.press(key)
        await pilot.pause(0.2)

        plugins = app.query_one(PluginsApp)
        assert plugins._filtering is True
        assert [option.id for option in plugins.query_one(OptionList).options] == [
            "plugin:lean"
        ]


@pytest.mark.asyncio
async def test_the_reload_binding_re_renders_the_open_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(config=build_test_vibe_config())
    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        _serve_catalog(app, monkeypatch, _catalog(_entry("productivity", "0bbb23a0")))
        await app._show_plugins()
        await pilot.pause(0.2)

        after = _catalog(_entry("productivity", "4c71ea55"), _entry("lean", "aa10bb20"))
        monkeypatch.setattr(
            app.app_server.resources.plugins,
            "reload",
            AsyncMock(
                return_value=PluginCatalogDiff(
                    changes=(PluginCatalogChange("lean", None, "aa10bb20"),),
                    state=after,
                )
            ),
        )
        await pilot.press("r")
        await pilot.pause(0.2)

        # The diff carries the after-image, so the re-render costs no third read.
        assert [entry.name for entry in app.query_one(PluginsApp)._state.plugins] == [
            "productivity",
            "lean",
        ]


@pytest.mark.asyncio
async def test_reloading_configuration_never_re_pins_the_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `_reload_config` has six callers, of which only one is `/reload`; folding
    # plugin reload into it would re-pin on a model switch.
    app = await _prepared_app()
    _capture_mounted(app, monkeypatch)
    reload_plugins = AsyncMock()
    monkeypatch.setattr(app.app_server.resources.plugins, "reload", reload_plugins)

    await app._reload_config()

    reload_plugins.assert_not_awaited()
