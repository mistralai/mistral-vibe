from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock

from vibe.cli.textual_ui.widgets.mcp_app import MCPApp, collect_mcp_tool_index
from vibe.core.config import MCPStdio
from vibe.core.tools.base import InvokeContext
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
