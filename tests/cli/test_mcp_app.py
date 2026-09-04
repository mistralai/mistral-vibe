from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList
from textual.worker import Worker

from vibe.app_server.models import (
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
    MCPToolSummary,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolError,
    ProtocolErrorCode,
)
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.mcp_app import (
    _LIST_VIEW_HELP_AUTH,
    _LIST_VIEW_HELP_TOOLS,
    _REFRESHING_LABEL,
    MCPApp,
    MCPOptionList,
    _filter_sources,
    _sort_sources_for_menu,
    _source_from_option_id,
    _source_option_id,
    _tool_count_text,
)
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic


def _source(
    name: str,
    *,
    kind: MCPSourceKind = MCPSourceKind.SERVER,
    status: MCPSourceStatus = MCPSourceStatus.CONNECTED,
    tools: list[MCPToolSummary] | None = None,
    error: str | None = None,
) -> MCPSourceSummary:
    return MCPSourceSummary(
        name=name,
        kind=kind,
        transport="connector" if kind is MCPSourceKind.CONNECTOR else "stdio",
        status=status,
        tools=tools or [],
        error=error,
    )


def _state(*sources: MCPSourceSummary) -> MCPState:
    return MCPState(sources=list(sources))


class MCPAppHarness(App[None]):
    def __init__(self, state: MCPState) -> None:
        super().__init__()
        self._state = state

    def compose(self) -> ComposeResult:
        yield MCPApp(self._state)


def test_initial_source_is_normalized() -> None:
    app = MCPApp(_state(_source("search")), initial_source="  search  ")

    assert app.id == "mcp-app"
    assert app._viewing_name == "search"


def test_source_option_ids_preserve_kind_and_name() -> None:
    option_id = _source_option_id("mistral:search", MCPSourceKind.CONNECTOR)

    assert _source_from_option_id(option_id) == (
        "mistral:search",
        MCPSourceKind.CONNECTOR,
    )
    assert _source_from_option_id("tool:search") is None


def test_source_sorting_puts_nonempty_sources_first_and_sorts_each_group() -> None:
    sources = [
        _source(
            "Zulu Empty", kind=MCPSourceKind.CONNECTOR, status=MCPSourceStatus.DISABLED
        ),
        _source(
            "zulu tools",
            kind=MCPSourceKind.CONNECTOR,
            status=MCPSourceStatus.NEEDS_AUTH,
            tools=[MCPToolSummary(name="search")],
        ),
        _source(
            "Alpha Empty",
            kind=MCPSourceKind.CONNECTOR,
            status=MCPSourceStatus.CONNECTED,
        ),
        _source(
            "alpha tools",
            kind=MCPSourceKind.CONNECTOR,
            status=MCPSourceStatus.DISABLED,
            tools=[MCPToolSummary(name="search", enabled=False)],
        ),
    ]

    assert [source.name for source in _sort_sources_for_menu(sources)] == [
        "alpha tools",
        "zulu tools",
        "Alpha Empty",
        "Zulu Empty",
    ]


def test_filter_sources_fuzzy_matches_and_ranks_names() -> None:
    sources = [
        _source("Slack", kind=MCPSourceKind.CONNECTOR),
        _source("Google Drive", kind=MCPSourceKind.CONNECTOR),
        _source("GitHub", kind=MCPSourceKind.CONNECTOR),
    ]

    assert [source.name for source in _filter_sources(sources, "gd")] == [
        "Google Drive"
    ]


@pytest.mark.asyncio
async def test_overview_starts_on_first_source_and_search_wraps_like_a_row() -> None:
    app = MCPAppHarness(
        _state(
            _source("gmail", kind=MCPSourceKind.CONNECTOR),
            _source("slack", kind=MCPSourceKind.CONNECTOR),
        )
    )

    async with app.run_test() as pilot:
        option_list = app.query_one(MCPOptionList)
        search = app.query_one("#mcp-search", Input)
        search_icon = app.query_one("#mcp-search-icon", NoMarkupStatic)

        assert search_icon.content == "🔍"
        assert search.placeholder == "Search servers and connectors (← to focus)"
        assert app.screen.focused is option_list
        assert option_list.get_option_at_index(option_list.highlighted or 0).id == (
            "connector:gmail"
        )

        await pilot.press("up")
        assert app.screen.focused is search

        await pilot.press("up")
        assert app.screen.focused is option_list
        assert option_list.get_option_at_index(option_list.highlighted or 0).id == (
            "connector:slack"
        )

        option_list.scroll_to = MagicMock(wraps=option_list.scroll_to)
        await pilot.press("left")
        assert app.screen.focused is search
        option_list.scroll_to.assert_any_call(
            y=0, animate=False, force=True, immediate=True
        )

        await pilot.press("down")
        assert app.screen.focused is option_list
        assert option_list.get_option_at_index(option_list.highlighted or 0).id == (
            "connector:gmail"
        )

        await pilot.press("up", "up")
        assert app.screen.focused is option_list
        assert option_list.get_option_at_index(option_list.highlighted or 0).id == (
            "connector:slack"
        )

        await pilot.press("down")
        assert app.screen.focused is search

        await pilot.press("down")
        assert app.screen.focused is option_list
        assert option_list.get_option_at_index(option_list.highlighted or 0).id == (
            "connector:gmail"
        )


@pytest.mark.asyncio
async def test_search_fuzzy_filters_sources_without_taking_initial_focus() -> None:
    app = MCPAppHarness(
        _state(
            _source("Google Drive", kind=MCPSourceKind.CONNECTOR),
            _source("GitHub", kind=MCPSourceKind.CONNECTOR),
            _source("Slack", kind=MCPSourceKind.CONNECTOR),
        )
    )

    async with app.run_test() as pilot:
        option_list = app.query_one(MCPOptionList)
        await pilot.press("up", "g", "d")

        source_ids = [
            option.id
            for option in option_list.options
            if option.id is not None and option.id.startswith("connector:")
        ]
        assert app.screen.focused is app.query_one("#mcp-search", Input)
        assert source_ids == ["connector:Google Drive"]

        await pilot.press("down")
        option_list.scroll_to = MagicMock(wraps=option_list.scroll_to)
        await pilot.press("up")

        assert app.screen.focused is app.query_one("#mcp-search", Input)
        option_list.scroll_to.assert_any_call(
            y=0, animate=False, force=True, immediate=True
        )


def test_list_view_sorts_server_and_connector_groups_by_discovered_tools() -> None:
    app = MCPApp(
        _state(
            _source("server-empty"),
            _source("server-tools", tools=[MCPToolSummary(name="search")]),
            _source("connector-empty", kind=MCPSourceKind.CONNECTOR),
            _source(
                "connector-tools",
                kind=MCPSourceKind.CONNECTOR,
                tools=[MCPToolSummary(name="search")],
            ),
        )
    )
    app.query_one = MagicMock()
    app._set_help_text = MagicMock()
    app._add_source_group = MagicMock()

    app._show_list_view(MagicMock())

    groups = app._add_source_group.call_args_list
    assert [source.name for source in groups[0].args[2]] == [
        "server-tools",
        "server-empty",
    ]
    assert [source.name for source in groups[1].args[2]] == [
        "connector-tools",
        "connector-empty",
    ]


def test_tool_count_text_distinguishes_partial_and_empty_sources() -> None:
    assert _tool_count_text(1, 2) == "1/2 tools"
    assert _tool_count_text(0, 0) == "no tools"
    assert _tool_count_text(1, 1) == "1 tool"


def test_highlighting_auth_source_changes_help() -> None:
    source = _source("oauth", status=MCPSourceStatus.NEEDS_AUTH)
    app = MCPApp(_state(source))
    app._viewing_name = None
    app.query_one = MagicMock(return_value=MagicMock(highlighted=1))
    app._source_for_option = MagicMock(return_value=source)
    app._set_help_text = MagicMock()
    event = MagicMock()

    app.on_option_list_option_highlighted(event)

    app._set_help_text.assert_called_once_with(_LIST_VIEW_HELP_AUTH)


def test_highlighting_regular_source_uses_tool_help() -> None:
    source = _source("local")
    app = MCPApp(_state(source))
    app._viewing_name = None
    app.query_one = MagicMock(return_value=MagicMock(highlighted=0))
    app._source_for_option = MagicMock(return_value=source)
    app._set_help_text = MagicMock()
    event = MagicMock()

    app.on_option_list_option_highlighted(event)

    app._set_help_text.assert_called_once_with(_LIST_VIEW_HELP_TOOLS)


def test_oauth_source_detail_requests_server_auth() -> None:
    source = _source("oauth", status=MCPSourceStatus.NEEDS_AUTH)
    app = MCPApp(_state(source))
    app.query_one = MagicMock()
    app._set_help_text = MagicMock()
    app.post_message = MagicMock()

    app._show_detail_view(MagicMock(), source)

    message = app.post_message.call_args.args[0]
    assert isinstance(message, MCPApp.MCPOAuthRequested)
    assert message.server_name == "oauth"


def test_connector_detail_requests_connector_auth() -> None:
    source = _source(
        "gmail", kind=MCPSourceKind.CONNECTOR, status=MCPSourceStatus.NEEDS_AUTH
    )
    app = MCPApp(_state(source))
    app.query_one = MagicMock()
    app._set_help_text = MagicMock()
    app.post_message = MagicMock()

    app._show_detail_view(MagicMock(), source)

    message = app.post_message.call_args.args[0]
    assert isinstance(message, MCPApp.ConnectorAuthRequested)
    assert message.connector_name == "gmail"


def test_connector_detail_shows_bootstrap_error() -> None:
    source = _source(
        "slack",
        kind=MCPSourceKind.CONNECTOR,
        status=MCPSourceStatus.UNAVAILABLE,
        error="Slack OAuth token expired",
    )
    app = MCPApp(_state(source))
    app.query_one = MagicMock()
    app._set_help_text = MagicMock()
    app.post_message = MagicMock()
    option_list = MagicMock()

    app._show_detail_view(option_list, source)

    labels = " ".join(
        str(call.args[0].prompt) for call in option_list.add_option.call_args_list
    )
    assert "Failed to bootstrap" in labels
    assert "Slack OAuth token expired" in labels


def test_connector_detail_shows_error_over_needs_auth() -> None:
    source = _source(
        "slack",
        kind=MCPSourceKind.CONNECTOR,
        status=MCPSourceStatus.NEEDS_AUTH,
        error="bootstrap failed: upstream 500",
    )
    app = MCPApp(_state(source))
    app.query_one = MagicMock()
    app._set_help_text = MagicMock()
    app.post_message = MagicMock()
    option_list = MagicMock()

    app._show_detail_view(option_list, source)

    labels = " ".join(
        str(call.args[0].prompt) for call in option_list.add_option.call_args_list
    )
    assert "Failed to bootstrap" in labels
    assert "upstream 500" in labels
    app.post_message.assert_not_called()


def test_connector_detail_shows_error_over_needs_setup() -> None:
    source = _source(
        "slack",
        kind=MCPSourceKind.CONNECTOR,
        status=MCPSourceStatus.NEEDS_SETUP,
        error="bootstrap failed: missing credentials",
    )
    app = MCPApp(_state(source))
    app.query_one = MagicMock()
    app._set_help_text = MagicMock()
    app.post_message = MagicMock()
    option_list = MagicMock()

    app._show_detail_view(option_list, source)

    labels = " ".join(
        str(call.args[0].prompt) for call in option_list.add_option.call_args_list
    )
    assert "Failed to bootstrap" in labels
    assert "missing credentials" in labels


def test_toggling_source_posts_public_identity() -> None:
    source = _source("local")
    app = MCPApp(_state(source))
    app._highlighted_source = MagicMock(return_value=source)
    app._rebuild_preserving_scroll = MagicMock()
    app.post_message = MagicMock()

    app._set_highlighted_disabled(disabled=True)

    message = app.post_message.call_args.args[0]
    assert isinstance(message, MCPApp.MCPToggled)
    assert message.name == "local"
    assert message.kind is MCPSourceKind.SERVER
    assert message.disabled is True
    assert source.status is MCPSourceStatus.DISABLED


def test_toggling_tool_uses_remote_tool_name() -> None:
    source = _source(
        "local", tools=[MCPToolSummary(name="search", description="Search")]
    )
    app = MCPApp(_state(source))
    app._viewing_name = "local"
    app._viewing_kind = MCPSourceKind.SERVER
    option_list = MagicMock(highlighted=0)
    option_list.get_option_at_index.return_value.id = "tool:search"
    app.query_one = MagicMock(return_value=option_list)
    app._rebuild_preserving_scroll = MagicMock()
    app.post_message = MagicMock()

    app._set_highlighted_tool_disabled(disabled=True)

    message = app.post_message.call_args.args[0]
    assert isinstance(message, MCPApp.MCPToggled)
    assert message.tool_name == "search"
    assert app._state.sources[0].tools[0].enabled is False


def test_refresh_index_replaces_state_from_resource() -> None:
    initial = _state(_source("old"))
    updated = _state(_source("new"))
    app = MCPApp(initial, state_getter=lambda: updated)
    app._rebuild_preserving_scroll = MagicMock()

    app.refresh_index()

    assert [source.name for source in app._state.sources] == ["new"]
    app._rebuild_preserving_scroll.assert_called_once()


def test_start_refresh_dispatches_one_worker() -> None:
    app = MCPApp(_state(), refresh_callback=AsyncMock(return_value="Refreshed"))

    def close_worker(coroutine, **_kwargs: object) -> None:
        coroutine.close()

    app.run_worker = MagicMock(side_effect=close_worker)

    app._start_refresh()
    app._start_refresh()

    assert app._refreshing is True
    app.run_worker.assert_called_once()
    assert app.run_worker.call_args.kwargs["exit_on_error"] is False


def test_mount_waits_for_interval_before_refreshing() -> None:
    app = MCPApp(_state(), refresh_callback=AsyncMock(return_value="Refreshed"))
    app._refresh_view = MagicMock()
    app.query_one = MagicMock(return_value=MagicMock())
    app._start_refresh = MagicMock()
    app.set_interval = MagicMock()

    app.on_mount()

    app._start_refresh.assert_not_called()
    app.set_interval.assert_called_once_with(60.0, app._start_refresh)


def test_finished_refresh_rebuilds_only_while_attached() -> None:
    app = MCPApp(_state())
    app._refreshing = True
    app.refresh_index = MagicMock()
    worker = MagicMock(spec=Worker, group="refresh", is_finished=True)
    event = MagicMock(spec=Worker.StateChanged, worker=worker)

    with patch.object(
        MCPApp, "is_attached", new_callable=PropertyMock, return_value=True
    ):
        app.on_worker_state_changed(event)

    assert app._refreshing is False
    app.refresh_index.assert_called_once()


def test_unknown_source_falls_back_to_overview() -> None:
    app = MCPApp(_state(_source("known")))
    option_list = MagicMock(spec=OptionList)
    app.query_one = MagicMock(return_value=option_list)
    app._show_list_view = MagicMock()

    app._refresh_view("missing")

    app._show_list_view.assert_called_once_with(option_list)


def test_unavailable_server_with_no_tools_shows_discovery_failed_label() -> None:
    source = _source("broken", status=MCPSourceStatus.UNAVAILABLE, tools=[])
    app = MCPApp(_state(source))
    option_list = MagicMock()

    app._add_source_group(option_list, "Local MCP Servers", [source])

    calls = option_list.add_option.call_args_list
    # First call is the group title; second is the source row
    source_label = calls[1].args[0].prompt
    assert "tool discovery failed" in source_label.plain


def test_unavailable_server_with_tools_does_not_show_discovery_failed_label() -> None:
    source = _source(
        "partial",
        status=MCPSourceStatus.UNAVAILABLE,
        tools=[MCPToolSummary(name="search")],
    )
    app = MCPApp(_state(source))
    option_list = MagicMock()

    app._add_source_group(option_list, "Local MCP Servers", [source])

    calls = option_list.add_option.call_args_list
    source_label = calls[1].args[0].prompt
    assert "tool discovery failed" not in source_label.plain


def test_unavailable_connector_with_no_tools_keeps_tool_count_label() -> None:
    source = _source(
        "gmail",
        kind=MCPSourceKind.CONNECTOR,
        status=MCPSourceStatus.UNAVAILABLE,
        tools=[],
    )
    app = MCPApp(_state(source))
    option_list = MagicMock()

    app._add_source_group(option_list, "Workspace Connectors", [source])

    calls = option_list.add_option.call_args_list
    source_label = calls[1].args[0].prompt
    assert "tool discovery failed" not in source_label.plain


def test_detail_view_unavailable_server_shows_discovery_failed() -> None:
    source = _source("broken", status=MCPSourceStatus.UNAVAILABLE, tools=[])
    app = MCPApp(_state(source))
    app.query_one = MagicMock()
    app._set_help_text = MagicMock()
    option_list = MagicMock()

    app._show_detail_view(option_list, source)

    calls = option_list.add_option.call_args_list
    assert "Tool discovery failed" in calls[0].args[0].prompt


def test_detail_view_unavailable_server_shows_discovery_error_message() -> None:
    source = _source("broken", status=MCPSourceStatus.UNAVAILABLE, tools=[])
    state = MCPState(
        sources=[source], discovery_errors={"broken": "spawn nonexistent-binary ENOENT"}
    )
    app = MCPApp(state)
    app.query_one = MagicMock()
    app._set_help_text = MagicMock()
    option_list = MagicMock()

    app._show_detail_view(option_list, source)

    calls = option_list.add_option.call_args_list
    assert "Tool discovery failed" in calls[0].args[0].prompt
    assert "spawn nonexistent-binary ENOENT" in str(calls[1].args[0].prompt)


def test_detail_view_connected_server_shows_no_tools_discovered() -> None:
    source = _source("empty", status=MCPSourceStatus.CONNECTED, tools=[])
    app = MCPApp(_state(source))
    app.query_one = MagicMock()
    app._set_help_text = MagicMock()
    option_list = MagicMock()

    app._show_detail_view(option_list, source)

    calls = option_list.add_option.call_args_list
    assert "No tools discovered" in calls[0].args[0].prompt


def test_a_plugin_owned_server_says_which_plugin_declared_it() -> None:
    plugin_owned = _source("figma", status=MCPSourceStatus.NEEDS_AUTH)
    plugin_owned.plugin_name = "figma"
    configured = _source("local")
    app = MCPApp(_state(plugin_owned, configured))
    option_list = MagicMock()

    app._add_source_group(option_list, "Local MCP Servers", [plugin_owned, configured])

    rows = [call.args[0].prompt.plain for call in option_list.add_option.call_args_list]
    assert "[plugin:figma]" in rows[1]
    assert "[plugin:" not in rows[2]


def test_a_group_of_configured_servers_claims_no_owner_column() -> None:
    source = _source("local")
    app = MCPApp(_state(source))
    option_list = MagicMock()

    app._add_source_group(option_list, "Local MCP Servers", [source])

    row = option_list.add_option.call_args_list[1].args[0].prompt.plain
    assert row == "  local  [stdio]  no tools  ● connected"


def test_a_plugin_owned_server_refuses_the_toggle_instead_of_posting_it() -> None:
    source = _source("figma")
    source.plugin_name = "figma"
    app = MCPApp(_state(source))
    app._highlighted_source = MagicMock(return_value=source)
    app._rebuild_preserving_scroll = MagicMock()
    app.post_message = MagicMock()
    app.notify = MagicMock()

    app._set_highlighted_disabled(disabled=True)

    app.post_message.assert_not_called()
    assert source.status is MCPSourceStatus.CONNECTED
    assert "managed by the figma plugin" in app.notify.call_args.args[0]


def test_a_plugin_owned_tool_row_refuses_the_toggle_instead_of_posting_it() -> None:
    # Prepare
    source = _source(
        "figma", tools=[MCPToolSummary(name="get_metadata", description="Metadata")]
    )
    source.plugin_name = "figma"
    app = MCPApp(_state(source))
    app._viewing_name = "figma"
    app._viewing_kind = MCPSourceKind.SERVER
    option_list = MagicMock(highlighted=0)
    option_list.get_option_at_index.return_value.id = "tool:get_metadata"
    app.query_one = MagicMock(return_value=option_list)
    app._rebuild_preserving_scroll = MagicMock()
    app.post_message = MagicMock()
    app.notify = MagicMock()

    # Do
    app._set_highlighted_disabled(disabled=True)

    # Assert
    app.post_message.assert_not_called()
    assert app._state.sources[0].tools[0].enabled is True
    assert "managed by the figma plugin" in app.notify.call_args.args[0]


@pytest.mark.asyncio
async def test_a_rejected_toggle_is_shown_to_the_user_rather_than_raised() -> None:
    # Prepare
    app = MagicMock()
    rejection = AppServerResponseError(
        ProtocolError(
            code=ProtocolErrorCode.INVALID_PARAMS,
            message=(
                "MCP server 'linear-plugin' is managed by the 'linear-probe' "
                "plugin and cannot be toggled or removed from the MCP catalog."
            ),
        )
    )
    app.app_server.resources.mcp.toggle = AsyncMock(side_effect=rejection)
    message = MCPApp.MCPToggled(
        name="linear-plugin", kind=MCPSourceKind.SERVER, disabled=True
    )

    # Do
    await VibeApp.on_mcpapp_mcptoggled(app, message)

    # Assert
    assert "managed by the 'linear-probe' plugin" in app.notify.call_args.args[0]
    app.query_one.return_value.refresh_index.assert_called_once_with()
    app._refresh_banner.assert_called_once_with()


@pytest.mark.asyncio
async def test_enabling_a_failing_server_does_not_paint_it_enabled() -> None:
    app = MCPAppHarness(
        _state(
            _source("sentry", status=MCPSourceStatus.UNAVAILABLE),
            _source("linear", status=MCPSourceStatus.UNAVAILABLE),
        )
    )

    async with app.run_test() as pilot:
        await pilot.press("e")
        await pilot.pause()

        statuses = {
            source.name: source.status
            for source in app.query_one(MCPApp)._state.sources
        }
        assert statuses == {
            "sentry": MCPSourceStatus.UNAVAILABLE,
            "linear": MCPSourceStatus.UNAVAILABLE,
        }


def test_enabling_still_reports_the_toggle_to_the_host() -> None:
    source = _source("local", status=MCPSourceStatus.DISABLED)
    app = MCPApp(_state(source))
    app._highlighted_source = MagicMock(return_value=source)
    app._rebuild_preserving_scroll = MagicMock()
    app.post_message = MagicMock()

    app._set_highlighted_disabled(disabled=False)

    message = app.post_message.call_args.args[0]
    assert isinstance(message, MCPApp.MCPToggled)
    assert message.name == "local"
    assert message.disabled is False


@pytest.mark.asyncio
async def test_list_title_marks_an_in_flight_refresh() -> None:
    release = asyncio.Event()
    state = _state(_source("sentry", status=MCPSourceStatus.UNAVAILABLE))

    async def refresh() -> str:
        await release.wait()
        return "Refreshed."

    class Harness(App[None]):
        def compose(self) -> ComposeResult:
            yield MCPApp(state, state_getter=lambda: state, refresh_callback=refresh)

    app = Harness()
    async with app.run_test() as pilot:
        title = app.query_one("#mcp-title", NoMarkupStatic)
        assert _REFRESHING_LABEL not in str(title.content)

        app.query_one(MCPApp)._start_refresh()
        await pilot.pause()
        assert _REFRESHING_LABEL in str(title.content)

        release.set()
        await pilot.pause()
        await pilot.pause()

        assert _REFRESHING_LABEL not in str(title.content)


@pytest.mark.asyncio
async def test_starting_a_refresh_does_not_repost_a_detail_auth_request() -> None:
    state = _state(_source("sentry", status=MCPSourceStatus.NEEDS_AUTH))
    release = asyncio.Event()

    async def refresh() -> str:
        await release.wait()
        return "Refreshed."

    class Harness(App[None]):
        def __init__(self) -> None:
            super().__init__()
            self.auth_requests: list[str] = []

        def compose(self) -> ComposeResult:
            yield MCPApp(
                state,
                initial_source="sentry",
                state_getter=lambda: state,
                refresh_callback=refresh,
            )

        async def on_mcpapp_mcpoauth_requested(
            self, message: MCPApp.MCPOAuthRequested
        ) -> None:
            self.auth_requests.append(message.server_name)

    app = Harness()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.auth_requests == ["sentry"]

        app.query_one(MCPApp)._start_refresh()
        await pilot.pause()

        assert app.auth_requests == ["sentry"]

        release.set()
        await pilot.pause()
