from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.stubs.fake_connector_registry import FakeConnectorRegistry
from vibe.cli.textual_ui.widgets.mcp_app import (
    MCPApp,
    _sort_connector_names_for_menu,
    collect_mcp_tool_index,
)
from vibe.core.config import MCPStdio
from vibe.core.tools.base import InvokeContext
from vibe.core.tools.connectors.connector_registry import RemoteTool
from vibe.core.tools.mcp.tools import MCPTool, MCPToolResult, _OpenArgs
from vibe.core.types import ToolStreamEvent


def _make_tool_cls(
    *,
    is_mcp: bool,
    description: str = "",
    server_name: str | None = None,
    remote_name: str = "tool",
) -> type:
    if not is_mcp:
        return type("FakeTool", (), {"description": description})

    async def _run(
        self: Any, args: _OpenArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | MCPToolResult, None]:
        yield MCPToolResult(ok=True, server="", tool="", text=None)

    return type(
        "FakeMCPTool",
        (MCPTool,),
        {
            "description": description,
            "_server_name": server_name,
            "_remote_name": remote_name,
            "run": _run,
        },
    )


def _make_tool_manager(
    all_tools: dict[str, type], available_tools: dict[str, type] | None = None
) -> MagicMock:
    mgr = MagicMock()
    mgr.registered_tools = all_tools
    mgr.available_tools = available_tools if available_tools is not None else all_tools
    return mgr


class TestCollectMcpToolIndex:
    def test_non_mcp_tools_are_excluded(self) -> None:
        servers = [MCPStdio(name="srv", transport="stdio", command="cmd")]
        all_tools = {
            "srv_tool": _make_tool_cls(is_mcp=True, server_name="srv"),
            "bash": _make_tool_cls(is_mcp=False),
        }
        mgr = _make_tool_manager(all_tools)

        index = collect_mcp_tool_index(servers, mgr)

        assert "bash" not in str(index.server_tools)
        assert len(index.server_tools["srv"]) == 1

    def test_counts_match_available_vs_all(self) -> None:
        servers = [MCPStdio(name="srv", transport="stdio", command="cmd")]
        tool_a = _make_tool_cls(is_mcp=True, server_name="srv", remote_name="tool_a")
        tool_b = _make_tool_cls(is_mcp=True, server_name="srv", remote_name="tool_b")
        all_tools = {"srv_tool_a": tool_a, "srv_tool_b": tool_b}
        available = {"srv_tool_a": tool_a}
        mgr = _make_tool_manager(all_tools, available)

        index = collect_mcp_tool_index(servers, mgr)

        assert len(index.server_tools["srv"]) == 2
        enabled = sum(
            1 for t, _ in index.server_tools["srv"] if t in index.enabled_tools
        )
        assert enabled == 1

    def test_tool_with_no_matching_server_is_skipped(self) -> None:
        servers = [MCPStdio(name="srv", transport="stdio", command="cmd")]
        all_tools = {"other_tool": _make_tool_cls(is_mcp=True, server_name="other")}
        mgr = _make_tool_manager(all_tools)

        index = collect_mcp_tool_index(servers, mgr)

        assert index.server_tools == {}

    def test_empty_servers_returns_empty(self) -> None:
        mgr = _make_tool_manager({
            "srv_tool": _make_tool_cls(is_mcp=True, server_name="srv")
        })
        index = collect_mcp_tool_index([], mgr)
        assert index.server_tools == {}


class TestMCPAppInit:
    def test_viewing_server_none_when_no_initial_server(self) -> None:
        mgr = _make_tool_manager({})
        app = MCPApp(mcp_servers=[], tool_manager=mgr)
        assert app._viewing_server is None

    def test_initial_server_stripped_and_stored(self) -> None:
        servers = [MCPStdio(name="srv", transport="stdio", command="cmd")]
        mgr = _make_tool_manager({})
        app = MCPApp(mcp_servers=servers, tool_manager=mgr, initial_server="  srv  ")
        assert app._viewing_server == "srv"

    def test_widget_id_is_mcp_app(self) -> None:
        mgr = _make_tool_manager({})
        app = MCPApp(mcp_servers=[], tool_manager=mgr)
        assert app.id == "mcp-app"

    def test_refresh_view_unknown_server_falls_back_to_overview(self) -> None:
        servers = [MCPStdio(name="srv", transport="stdio", command="cmd")]
        mgr = _make_tool_manager({})
        app = MCPApp(mcp_servers=servers, tool_manager=mgr)
        app.query_one = MagicMock()
        app._refresh_view("nonexistent")
        assert app._viewing_server is None

    def test_refresh_view_known_server_sets_viewing_server(self) -> None:
        servers = [MCPStdio(name="srv", transport="stdio", command="cmd")]
        mgr = _make_tool_manager({})
        app = MCPApp(mcp_servers=servers, tool_manager=mgr)
        app.query_one = MagicMock()
        app._refresh_view("srv")
        assert app._viewing_server == "srv"

    def test_refresh_view_none_clears_viewing_server(self) -> None:
        servers = [MCPStdio(name="srv", transport="stdio", command="cmd")]
        mgr = _make_tool_manager({})
        app = MCPApp(mcp_servers=servers, tool_manager=mgr)
        app._viewing_server = "srv"
        app.query_one = MagicMock()
        app._refresh_view(None)
        assert app._viewing_server is None

    def test_action_back_calls_refresh_view_none(self) -> None:
        servers = [MCPStdio(name="srv", transport="stdio", command="cmd")]
        mgr = _make_tool_manager({})
        app = MCPApp(mcp_servers=servers, tool_manager=mgr)
        app._viewing_server = "srv"
        render_calls: list[str | None] = []
        app._refresh_view = lambda server_name, *, kind=None: render_calls.append(
            server_name
        )
        app.action_back()
        assert render_calls == [None]

    def test_action_back_noop_when_in_overview(self) -> None:
        mgr = _make_tool_manager({})
        app = MCPApp(mcp_servers=[], tool_manager=mgr)
        app._viewing_server = None
        render_calls: list[str | None] = []
        app._refresh_view = lambda server_name, *, kind=None: render_calls.append(
            server_name
        )
        app.action_back()
        assert render_calls == []

    @pytest.mark.asyncio
    async def test_action_refresh_dispatches_worker(self) -> None:
        servers = [MCPStdio(name="srv", transport="stdio", command="cmd")]
        mgr = _make_tool_manager({})
        refresh_callback = AsyncMock(return_value="Refreshed.")
        app = MCPApp(
            mcp_servers=servers, tool_manager=mgr, refresh_callback=refresh_callback
        )
        app._viewing_server = "srv"
        render_calls: list[tuple[str | None, str | None]] = []
        app._refresh_view = lambda server_name, *, kind=None: render_calls.append((
            server_name,
            kind,
        ))
        app.run_worker = MagicMock()

        await app.action_refresh()

        assert app._status_message == "Refreshing..."
        assert render_calls == [("srv", None)]
        app.run_worker.assert_called_once()

    def test_on_worker_state_changed_updates_after_refresh(self) -> None:
        from textual.worker import Worker

        servers = [MCPStdio(name="srv", transport="stdio", command="cmd")]
        mgr = _make_tool_manager({})
        app = MCPApp(mcp_servers=servers, tool_manager=mgr)
        app.refresh_index = MagicMock()

        worker = MagicMock(spec=Worker)
        worker.group = "refresh"
        worker.is_finished = True
        worker.result = "Refreshed."
        event = MagicMock(spec=Worker.StateChanged)
        event.worker = worker

        app.on_worker_state_changed(event)

        assert app._status_message == "Refreshed."
        assert app._refreshing is False
        app.refresh_index.assert_called_once()

    def test_close_blocked_while_refreshing(self) -> None:
        mgr = _make_tool_manager({})
        app = MCPApp(mcp_servers=[], tool_manager=mgr)
        app._refreshing = True
        app.post_message = MagicMock()

        app.action_close()

        app.post_message.assert_not_called()

    def test_back_blocked_while_refreshing(self) -> None:
        servers = [MCPStdio(name="srv", transport="stdio", command="cmd")]
        mgr = _make_tool_manager({})
        app = MCPApp(mcp_servers=servers, tool_manager=mgr)
        app._viewing_server = "srv"
        app._refreshing = True
        render_calls: list[str | None] = []
        app._refresh_view = lambda server_name, *, kind=None: render_calls.append(
            server_name
        )

        app.action_back()

        assert render_calls == []


class TestConnectorMenuOrdering:
    def test_connectors_are_sorted_by_connected_state_then_name(self) -> None:
        registry = FakeConnectorRegistry(
            connectors={
                "zeta": [],
                "alpha": [RemoteTool(name="lookup", description="Lookup")],
                "beta": [],
            }
        )

        ordered = _sort_connector_names_for_menu(
            registry.get_connector_names(), registry
        )

        assert ordered == ["alpha", "beta", "zeta"]

    def test_sorting_is_case_insensitive(self) -> None:
        registry = FakeConnectorRegistry(
            connectors={
                "Zeta": [],
                "alpha": [RemoteTool(name="lookup", description="Lookup")],
                "Beta": [],
            }
        )

        ordered = _sort_connector_names_for_menu(
            registry.get_connector_names(), registry
        )

        assert ordered == ["alpha", "Beta", "Zeta"]
