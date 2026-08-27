from __future__ import annotations

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
from vibe.cli.textual_ui.widgets.mcp_app import (
    _LIST_VIEW_HELP_AUTH,
    _LIST_VIEW_HELP_TOOLS,
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
