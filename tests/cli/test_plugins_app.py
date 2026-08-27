from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

from vibe.app_server.models import (
    PluginCatalogComponent,
    PluginCatalogDropped,
    PluginCatalogEntry,
    PluginCatalogState,
    PluginScope,
)
from vibe.app_server.resources import PluginCatalogChange, PluginCatalogDiff
from vibe.cli.textual_ui.widgets.plugins_app import (
    _DETAIL_VIEW_HELP,
    _FILTER_HELP,
    _LIST_VIEW_HELP,
    PluginsApp,
    plugin_reload_report,
)


def _entry(
    name: str,
    *,
    version: str | None = "1.0.0",
    scope: PluginScope | None = "project",
    description: str = "",
    author: str | None = None,
    digest: str | None = "0bbb23a0abcdef",
    installed_root: str | None = "/tmp/plugins/productivity",
    components: list[PluginCatalogComponent] | None = None,
    drifted: int = 0,
) -> PluginCatalogEntry:
    return PluginCatalogEntry(
        name=name,
        version=version,
        source_format="agent_plugins_1_0",
        manifest_digest=f"manifest-{name}",
        description=description,
        author=author,
        scope=scope,
        content_sha256=digest,
        pinned_root=f"/tmp/pinned/{name}",
        installed_root=installed_root,
        components=components or [],
        drifted=drifted,
    )


def _state(
    *entries: PluginCatalogEntry, dropped: list[PluginCatalogDropped] | None = None
) -> PluginCatalogState:
    return PluginCatalogState(plugins=list(entries), dropped=dropped or [])


def _headless(app: PluginsApp) -> MagicMock:
    """Drive a view without mounting it, the way the MCP widget tests do."""
    app.query_one = MagicMock()
    app._filter_input = MagicMock(return_value=MagicMock(display=False))
    app._set_title = MagicMock()
    app._set_help_text = MagicMock()
    return MagicMock()


def _mocked(method: object) -> MagicMock:
    """Read a method ``_headless`` replaced back as the mock it now is."""
    return cast(MagicMock, method)


def _labels(option_list: MagicMock) -> list[str]:
    return [str(call.args[0].prompt) for call in option_list.add_option.call_args_list]


def test_the_list_names_every_plugin_with_the_facts_that_predict_behaviour() -> None:
    app = PluginsApp(
        _state(
            _entry("productivity"),
            _entry("frontend", scope="global", digest="1e040938ff"),
        )
    )
    option_list = _headless(app)

    app._show_list_view(option_list)

    assert _labels(option_list) == [
        "  productivity  project · agent_plugins_1_0 · 0bbb23a0",
        "  frontend      global · agent_plugins_1_0 · 1e040938",
    ]
    _mocked(app._set_title).assert_called_once_with("Plugins · 2 in this session")
    _mocked(app._set_help_text).assert_called_once_with(_LIST_VIEW_HELP)


def test_the_badges_that_vary_take_the_space_a_status_column_would() -> None:
    app = PluginsApp(
        _state(_entry("productivity", drifted=1), _entry("lean", installed_root=None))
    )
    option_list = _headless(app)

    app._show_list_view(option_list)

    labels = _labels(option_list)
    assert "⚠ 1 drifted" in labels[0]
    assert "uninstalled since pin" in labels[1]


def test_a_plugin_this_resolve_dropped_is_listed_from_what_the_pin_recorded() -> None:
    ghost = _entry("productivity", scope=None, digest=None)
    app = PluginsApp(_state(ghost))
    option_list = _headless(app)

    app._show_list_view(option_list)

    # Its tools still answer, so hiding it would hide what the session can name.
    assert _labels(option_list) == ["  productivity  — · agent_plugins_1_0 · —"]


def test_a_dropped_plugin_is_reported_below_the_list_and_cannot_be_opened() -> None:
    app = PluginsApp(
        _state(
            _entry("productivity"),
            dropped=[
                PluginCatalogDropped(
                    file=".vibe/plugins/broken/plugin.json",
                    message="plugin name must be lowercase",
                )
            ],
        )
    )
    option_list = _headless(app)

    app._show_list_view(option_list)

    assert "Not loaded" in _labels(option_list)
    assert any(
        "plugin name must be lowercase" in label for label in _labels(option_list)
    )
    assert all(
        call.args[0].disabled for call in option_list.add_option.call_args_list[1:]
    )


def test_an_empty_list_and_an_empty_filter_result_read_differently() -> None:
    app = PluginsApp(_state())
    option_list = _headless(app)

    app._show_list_view(option_list)
    assert _labels(option_list) == ["No plugins in this session"]

    filtered = PluginsApp(_state(_entry("productivity")))
    filtered._query = "absent"
    option_list = _headless(filtered)

    filtered._show_list_view(option_list)
    assert _labels(option_list) == ["No plugins match this filter"]


def test_the_detail_view_answers_where_a_plugin_came_from_and_what_it_brought() -> None:
    entry = _entry(
        "frontend",
        version=None,
        scope="global",
        description="Frontend design skill for UI/UX implementation",
        author="Anthropic",
        digest="1e040938ff",
        installed_root="/tmp/plugins/frontend",
        components=[
            PluginCatalogComponent(kind="skill", name="frontend-design"),
            PluginCatalogComponent(kind="mcp_server", name="records"),
            PluginCatalogComponent(kind="tool", name="plugin_frontend_records.lookup"),
        ],
    )
    app = PluginsApp(_state(entry))
    option_list = _headless(app)

    app._show_detail_view(option_list, entry)

    assert _labels(option_list) == [
        "  Scope: global",
        "  Format: agent_plugins_1_0",
        "  Frontend design skill for UI/UX implementation",
        "",
        "  Author: Anthropic",
        "  Version: —",
        "  Pinned: 1e040938",
        "  Installed from: /tmp/plugins/frontend",
        "",
        "  Components:",
        "  ● Skills: frontend-design",
        "  ● MCP servers: records",
        "  ● Tools: plugin_frontend_records.lookup",
    ]
    _mocked(app._set_title).assert_called_once_with("frontend")
    _mocked(app._set_help_text).assert_called_once_with(_DETAIL_VIEW_HELP)


def test_a_tool_that_drifted_carries_its_status_and_a_live_one_does_not() -> None:
    entry = _entry(
        "productivity",
        components=[
            PluginCatalogComponent(kind="tool", name="records.lookup", status="stale"),
            PluginCatalogComponent(kind="tool", name="reports.lookup"),
        ],
    )
    app = PluginsApp(_state(entry))
    option_list = _headless(app)

    app._show_detail_view(option_list, entry)

    assert "  ● Tools: records.lookup (stale), reports.lookup" in _labels(option_list)


def test_a_plugin_uninstalled_since_the_pin_says_so_where_its_path_would_be() -> None:
    entry = _entry("lean", installed_root=None)
    app = PluginsApp(_state(entry))
    option_list = _headless(app)

    app._show_detail_view(option_list, entry)

    assert "  Installed from: (uninstalled since pin)" in _labels(option_list)


def test_selecting_a_plugin_opens_it_and_backspace_returns_to_the_list() -> None:
    app = PluginsApp(_state(_entry("productivity")))
    app._refresh_view = MagicMock()

    app.on_option_list_option_selected(
        MagicMock(option=MagicMock(id="plugin:productivity"))
    )
    assert app._viewing_name == "productivity"

    app.action_back()
    assert app._viewing_name is None


def test_a_selection_that_is_not_a_plugin_row_opens_nothing() -> None:
    app = PluginsApp(_state(_entry("productivity")))
    app._refresh_view = MagicMock()

    app.on_option_list_option_selected(MagicMock(option=MagicMock(id=None)))

    assert app._viewing_name is None
    app._refresh_view.assert_not_called()


def test_the_filter_narrows_on_name_and_on_description() -> None:
    app = PluginsApp(
        _state(
            _entry("productivity", description="Reusable workflows."),
            _entry("frontend", description="UI and UX."),
        )
    )
    option_list = _headless(app)
    app._query = "ux"

    app._show_list_view(option_list)

    assert _labels(option_list) == [
        "  frontend  project · agent_plugins_1_0 · 0bbb23a0"
    ]


def test_escape_clears_an_open_filter_before_it_closes_the_list() -> None:
    app = PluginsApp(_state(_entry("productivity")))
    filter_input = MagicMock(display=True, value="prod")
    app._filter_input = MagicMock(return_value=filter_input)
    app._refresh_view = MagicMock()
    app.query_one = MagicMock()
    app.post_message = MagicMock()

    app.action_close()

    assert filter_input.value == "" and app._query == ""
    app.post_message.assert_not_called()

    filter_input.display = False
    app.action_close()

    assert isinstance(app.post_message.call_args.args[0], PluginsApp.PluginsClosed)


def test_the_filter_opens_only_over_the_list() -> None:
    app = PluginsApp(_state(_entry("productivity")))
    filter_input = MagicMock(display=False)
    app._filter_input = MagicMock(return_value=filter_input)
    app._set_help_text = MagicMock()

    app._viewing_name = "productivity"
    app.action_filter()
    assert filter_input.display is False

    app._viewing_name = None
    app.action_filter()
    assert filter_input.display is True
    _mocked(app._set_help_text).assert_called_once_with(_FILTER_HELP)


def test_the_reload_binding_asks_the_command_rather_than_reloading_itself() -> None:
    app = PluginsApp(_state(_entry("productivity")))
    app.post_message = MagicMock()

    app.action_reload()

    message = app.post_message.call_args.args[0]
    assert isinstance(message, PluginsApp.PluginsReloadRequested)


def test_a_reload_that_removes_the_open_plugin_falls_back_to_the_list() -> None:
    app = PluginsApp(_state(_entry("productivity"), _entry("lean")))
    app._viewing_name = "lean"
    option_list = _headless(app)
    app.query_one = MagicMock(return_value=option_list)

    app.update_state(_state(_entry("productivity")))

    assert app._viewing_name is None
    assert _labels(option_list) == [
        "  productivity  project · agent_plugins_1_0 · 0bbb23a0"
    ]


def _diff(
    *changes: PluginCatalogChange, state: PluginCatalogState
) -> PluginCatalogDiff:
    return PluginCatalogDiff(changes=changes, state=state)


def test_the_reload_report_names_what_moved_by_digest() -> None:
    report = plugin_reload_report(
        _diff(
            PluginCatalogChange("frontend", "1e040938ff", None),
            PluginCatalogChange("lean", None, "aa10bb20ff"),
            PluginCatalogChange("productivity", "0bbb23a0ff", "4c71ea55ff"),
            state=_state(_entry("lean", version="0.2.0"), _entry("productivity")),
        )
    )

    assert report == "\n".join((
        "### Plugins reloaded",
        "",
        "- `-` `frontend` — no longer installed",
        "- `+` `lean` 0.2.0",
        "- `~` `productivity` 0bbb23a0 → 4c71ea55",
    ))


def test_a_reload_that_moved_nothing_says_so_rather_than_listing_nothing() -> None:
    assert (
        plugin_reload_report(_diff(state=_state(_entry("productivity"))))
        == "Plugins reloaded. Nothing changed."
    )
