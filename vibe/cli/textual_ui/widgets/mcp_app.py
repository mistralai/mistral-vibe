from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.events import DescendantBlur
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.tools.connectors import ConnectorRegistry, connectors_enabled
from vibe.core.tools.mcp.tools import MCPTool

if TYPE_CHECKING:
    from vibe.core.config import MCPServer
    from vibe.core.tools.manager import ToolManager


class MCPToolIndex(NamedTuple):
    server_tools: dict[str, list[tuple[str, type[MCPTool]]]]
    connector_tools: dict[str, list[tuple[str, type[MCPTool]]]]
    enabled_tools: dict[str, type[Any]]


def collect_mcp_tool_index(
    mcp_servers: Sequence[MCPServer],
    tool_manager: ToolManager,
    connector_names: Sequence[str] = (),
) -> MCPToolIndex:
    registered = tool_manager.registered_tools
    available = tool_manager.available_tools
    configured_servers = {server.name for server in mcp_servers}
    connector_set = set(connector_names) if connectors_enabled() else set()
    server_tools: dict[str, list[tuple[str, type[MCPTool]]]] = {}
    connector_tools: dict[str, list[tuple[str, type[MCPTool]]]] = {}

    for tool_name, cls in registered.items():
        if not issubclass(cls, MCPTool):
            continue
        server_name = cls.get_server_name()
        if server_name is None:
            continue
        if cls.is_connector() and server_name in connector_set:
            connector_tools.setdefault(server_name, []).append((tool_name, cls))
        elif server_name in configured_servers:
            server_tools.setdefault(server_name, []).append((tool_name, cls))

    return MCPToolIndex(server_tools, connector_tools, enabled_tools=available)


class MCPApp(Container):
    can_focus_children = True
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=False),
        Binding("backspace", "back", "Back", show=False),
    ]

    class MCPClosed(Message):
        pass

    def __init__(
        self,
        mcp_servers: Sequence[MCPServer],
        tool_manager: ToolManager,
        initial_server: str = "",
        connector_registry: ConnectorRegistry | None = None,
    ) -> None:
        super().__init__(id="mcp-app")
        self._mcp_servers = mcp_servers
        self._connector_registry = connector_registry
        connector_names = (
            connector_registry.get_connector_names() if connector_registry else []
        )
        self._connector_names = connector_names
        self._tool_manager = tool_manager
        self._index = collect_mcp_tool_index(mcp_servers, tool_manager, connector_names)
        # Track both the name and the kind ("server" or "connector") to
        # disambiguate entries that share the same normalised name.
        self._viewing_server: str | None = initial_server.strip() or None
        self._viewing_kind: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="mcp-content"):
            yield NoMarkupStatic("", id="mcp-title", classes="settings-title")
            yield NoMarkupStatic("")
            yield OptionList(id="mcp-options")
            yield NoMarkupStatic("")
            yield NoMarkupStatic("", id="mcp-help", classes="settings-help")

    def on_mount(self) -> None:
        self._refresh_view(self._viewing_server)
        self.query_one(OptionList).focus()

    def refresh_index(self) -> None:
        """Re-snapshot the tool index (e.g. after deferred MCP discovery)."""
        if self._connector_registry:
            self._connector_names = self._connector_registry.get_connector_names()
        self._index = collect_mcp_tool_index(
            self._mcp_servers, self._tool_manager, self._connector_names
        )
        self._refresh_view(self._viewing_server, kind=self._viewing_kind)

    def on_descendant_blur(self, _event: DescendantBlur) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id or ""
        if option_id.startswith("server:"):
            self._refresh_view(option_id.removeprefix("server:"), kind="server")
        elif option_id.startswith("connector:"):
            self._refresh_view(option_id.removeprefix("connector:"), kind="connector")

    def action_back(self) -> None:
        if self._viewing_server is not None:
            self._refresh_view(None)

    def action_close(self) -> None:
        self.post_message(self.MCPClosed())

    # ── list view ────────────────────────────────────────────────────

    def _refresh_view(
        self, server_name: str | None, *, kind: str | None = None
    ) -> None:
        index = self._index
        option_list = self.query_one(OptionList)
        option_list.clear_options()

        server_names = {s.name for s in self._mcp_servers}
        all_names = server_names | set(self._connector_names)
        if server_name is None or server_name not in all_names:
            self._show_list_view(option_list, index)
            return

        # Infer kind when not provided (e.g. initial_server from /mcp <name>).
        # Prefer server over connector when the name is ambiguous.
        if kind is None:
            if server_name in server_names:
                kind = "server"
            else:
                kind = "connector"

        self._show_detail_view(server_name, option_list, index, kind=kind)

    def _show_list_view(self, option_list: OptionList, index: MCPToolIndex) -> None:
        self._viewing_server = None
        self._viewing_kind = None
        has_connectors = connectors_enabled() and bool(self._connector_names)
        title = "MCP Servers & Connectors" if has_connectors else "MCP Servers"
        self.query_one("#mcp-title", NoMarkupStatic).update(title)
        self.query_one("#mcp-help", NoMarkupStatic).update(
            "↑↓ Navigate  Enter Show tools  Esc Close"
        )

        has_servers = bool(self._mcp_servers)

        # ── Local MCP Servers ──
        if has_servers:
            max_name = max(len(srv.name) for srv in self._mcp_servers)
            max_type = max(len(srv.transport) + 2 for srv in self._mcp_servers)  # +[]
            option_list.add_option(
                Option(
                    Text("Local MCP Servers", style="bold", no_wrap=True), disabled=True
                )
            )
            for srv in self._mcp_servers:
                tools = index.server_tools.get(srv.name, [])
                enabled = sum(1 for t, _ in tools if t in index.enabled_tools)
                type_tag = f"[{srv.transport}]"
                label = Text(no_wrap=True)
                label.append(f"  {srv.name:<{max_name}}")
                label.append(f"  {type_tag:<{max_type}}", style="dim")
                label.append(f"  {_tool_count_text(enabled)}", style="dim")
                option_list.add_option(Option(label, id=f"server:{srv.name}"))

        # ── Workspace Connectors ──
        if has_connectors:
            max_name = max(len(n) for n in self._connector_names)
            type_tag = "[connector]"
            type_width = len(type_tag)
            tool_texts = {
                n: _tool_count_text(
                    sum(
                        1
                        for t, _ in index.connector_tools.get(n, [])
                        if t in index.enabled_tools
                    )
                )
                for n in self._connector_names
            }
            max_tools = max(len(t) for t in tool_texts.values())
            if has_servers:
                option_list.add_option(Option(Text("", no_wrap=True), disabled=True))
            option_list.add_option(
                Option(
                    Text("Workspace Connectors", style="bold", no_wrap=True),
                    disabled=True,
                )
            )
            for cname in self._connector_names:
                connected = (
                    self._connector_registry.is_connected(cname)
                    if self._connector_registry
                    else False
                )
                label = Text(no_wrap=True)
                label.append(f"  {cname:<{max_name}}")
                label.append(f"  {type_tag:<{type_width}}", style="dim")
                label.append(f"  {tool_texts[cname]:<{max_tools}}", style="dim")
                if connected:
                    label.append("  ")
                    label.append("●", style="green")
                    label.append(" connected", style="dim")
                else:
                    label.append("  ")
                    label.append("○", style="dim")
                    label.append(" not connected", style="dim")
                option_list.add_option(Option(label, id=f"connector:{cname}"))

        if not has_servers and not has_connectors:
            empty_msg = (
                "No MCP servers or connectors configured"
                if connectors_enabled()
                else "No MCP servers configured"
            )
            option_list.add_option(Option(empty_msg, disabled=True))

        if has_servers or has_connectors:
            # Skip disabled header options (e.g. section labels).
            first_enabled = next(
                (i for i, opt in enumerate(option_list._options) if not opt.disabled), 0
            )
            option_list.highlighted = first_enabled

    # ── detail view ──────────────────────────────────────────────────

    def _show_detail_view(
        self,
        server_name: str,
        option_list: OptionList,
        index: MCPToolIndex,
        *,
        kind: str = "server",
    ) -> None:
        self._viewing_server = server_name
        self._viewing_kind = kind
        is_connector = kind == "connector"
        title_prefix = "Connector" if is_connector else "MCP Server"
        self.query_one("#mcp-title", NoMarkupStatic).update(
            f"{title_prefix}: {server_name}"
        )
        self.query_one("#mcp-help", NoMarkupStatic).update(
            "↑↓ Navigate  Backspace Back  Esc Close"
        )
        tools_source = index.connector_tools if is_connector else index.server_tools
        all_tools = sorted(tools_source.get(server_name, []), key=lambda t: t[0])
        visible_tools = [(n, c) for n, c in all_tools if n in index.enabled_tools]
        if not visible_tools:
            option_list.add_option(
                Option("No enabled tools for this server", disabled=True)
            )
            return
        for tool_name, cls in visible_tools:
            remote_name = cls.get_remote_name()
            raw_desc = (
                (cls.description or "").removeprefix(f"[{server_name}] ").split("\n")[0]
            )
            label = Text(no_wrap=True)
            label.append(remote_name, style="bold")
            if raw_desc:
                label.append(f"  -  {raw_desc}")
            option_list.add_option(Option(label, id=f"tool:{tool_name}"))
        if visible_tools:
            option_list.highlighted = 0


def _tool_count_text(count: int) -> str:
    if count == 0:
        return "no tools"
    noun = "tool" if count == 1 else "tools"
    return f"{count} {noun}"
