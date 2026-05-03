from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.events import DescendantBlur
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option, OptionDoesNotExist
from textual.worker import Worker

from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.core.config import ConnectorConfig
from vibe.core.tools.connectors import ConnectorRegistry, connectors_enabled
from vibe.core.tools.mcp.tools import MCPTool
from vibe.core.tools.mcp_settings import updated_tool_list

if TYPE_CHECKING:
    from vibe.core.config import MCPServer
    from vibe.core.tools.manager import ToolManager


class MCPSourceKind(StrEnum):
    SERVER = auto()
    CONNECTOR = auto()


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


_LIST_VIEW_HELP_TOOLS = (
    "↑↓ Navigate  Enter Show tools  D Disable  E Enable  R Refresh  Esc Close"
)
_LIST_VIEW_HELP_AUTH = (
    "↑↓ Navigate  Enter Authenticate  D Disable  E Enable  R Refresh  Esc Close"
)
_DETAIL_VIEW_HELP = (
    "↑↓ Navigate  D Disable  E Enable  Backspace Back  R Refresh  Esc Close"
)


class MCPApp(Container):
    can_focus_children = True
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=False),
        Binding("backspace", "back", "Back", show=False),
        Binding("d", "disable", "Disable", show=False),
        Binding("e", "enable", "Enable", show=False),
        Binding("r", "refresh", "Refresh", show=False),
    ]

    class MCPClosed(Message):
        pass

    class MCPToggled(Message):
        """Posted when a server/connector or individual tool is toggled."""

        def __init__(
            self,
            name: str,
            kind: MCPSourceKind,
            disabled: bool,
            tool_name: str | None = None,
        ) -> None:
            super().__init__()
            self.name = name
            self.kind = kind
            self.disabled = disabled
            self.tool_name = tool_name

    class ConnectorAuthRequested(Message):
        """Posted when a disconnected connector needs authentication."""

        def __init__(
            self,
            connector_name: str,
            connector_registry: ConnectorRegistry,
            tool_manager: ToolManager,
        ) -> None:
            super().__init__()
            self.connector_name = connector_name
            self.connector_registry = connector_registry
            self.tool_manager = tool_manager

    def __init__(
        self,
        mcp_servers: Sequence[MCPServer],
        tool_manager: ToolManager,
        initial_server: str = "",
        connector_registry: ConnectorRegistry | None = None,
        get_connector_configs: Callable[[], list[ConnectorConfig]] | None = None,
        refresh_callback: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        super().__init__(id="mcp-app")
        self._mcp_servers = mcp_servers
        self._connector_registry = connector_registry
        self._get_connector_configs = get_connector_configs or (lambda: [])
        connector_names = (
            connector_registry.get_connector_names() if connector_registry else []
        )
        self._connector_names = connector_names
        self._sorted_connector_names = _sort_connector_names_for_menu(
            connector_names, connector_registry
        )
        self._tool_manager = tool_manager
        self._index = collect_mcp_tool_index(mcp_servers, tool_manager, connector_names)
        # Track both the name and the kind to disambiguate entries that
        # share the same normalised name.
        self._viewing_server: str | None = initial_server.strip() or None
        self._viewing_kind: MCPSourceKind | None = None
        self._refresh_callback = refresh_callback
        self._status_message: str | None = None
        self._refreshing = False

    def compose(self) -> ComposeResult:
        with Vertical(id="mcp-content"):
            yield NoMarkupStatic("", id="mcp-title", classes="settings-title")
            yield NoMarkupStatic("")
            yield OptionList(id="mcp-options")
            yield NoMarkupStatic("", id="mcp-help", classes="settings-help")

    def on_mount(self) -> None:
        self._refresh_view(self._viewing_server)
        self.query_one(OptionList).focus()

    def refresh_index(self) -> None:
        """Re-snapshot the tool index (e.g. after deferred MCP discovery)."""
        if self._connector_registry:
            self._connector_names = self._connector_registry.get_connector_names()
            self._sorted_connector_names = _sort_connector_names_for_menu(
                self._connector_names, self._connector_registry
            )
        self._rebuild_preserving_scroll()

    def on_descendant_blur(self, _event: DescendantBlur) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id or ""
        if option_id.startswith("server:"):
            self._refresh_view(
                option_id.removeprefix("server:"), kind=MCPSourceKind.SERVER
            )
        elif option_id.startswith("connector:"):
            self._refresh_view(
                option_id.removeprefix("connector:"), kind=MCPSourceKind.CONNECTOR
            )

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        option_list = self.query_one(OptionList)
        highlighted = option_list.highlighted
        if highlighted is None or highlighted == 0:
            return
        # When the first enabled option is highlighted and all options above
        # it are disabled headers, scroll to top so the header stays visible.
        if all(option_list.get_option_at_index(i).disabled for i in range(highlighted)):
            option_list.scroll_to(y=0, animate=False, force=True, immediate=True)
        # Update help text based on whether highlighted connector needs auth.
        if self._viewing_server is None:
            self._set_help_text(self._list_help_for_option(event.option))

    def action_back(self) -> None:
        if self._refreshing:
            return
        if self._viewing_server is not None:
            self._refresh_view(None)

    def action_close(self) -> None:
        if self._refreshing:
            return
        self.post_message(self.MCPClosed())

    async def action_refresh(self) -> None:
        if self._refresh_callback is None:
            return

        self._status_message = "Refreshing..."
        help = _DETAIL_VIEW_HELP if self._viewing_server else _LIST_VIEW_HELP_TOOLS
        self._set_help_text(help)

        self._refreshing = True
        self.run_worker(self._run_refresh(), exclusive=True, group="refresh")

    async def _run_refresh(self) -> str:
        if self._refresh_callback is None:
            return ""
        return await self._refresh_callback()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "refresh":
            return
        if event.worker.is_finished:
            self._refreshing = False
            result = event.worker.result
            self._status_message = result if isinstance(result, str) else "Refreshed."
            self.refresh_index()

    def _list_help_for_option(self, option: Option) -> str:
        """Return the appropriate list-view help text for the given option."""
        option_id = option.id or ""
        if option_id.startswith("connector:") and self._connector_registry:
            name = option_id.removeprefix("connector:")
            if not self._connector_registry.is_connected(name):
                return _LIST_VIEW_HELP_AUTH
        return _LIST_VIEW_HELP_TOOLS

    def _set_help_text(self, text: str) -> None:
        if self._status_message:
            text = f"{self._status_message}  {text}"
        self.query_one("#mcp-help", NoMarkupStatic).update(text)

    def _find_connector_config(self, name: str) -> ConnectorConfig | None:
        """Look up a connector config by name from the live VibeConfig source."""
        return next((c for c in self._get_connector_configs() if c.name == name), None)

    def action_disable(self) -> None:
        self._set_highlighted_disabled(disabled=True)

    def action_enable(self) -> None:
        self._set_highlighted_disabled(disabled=False)

    def _set_highlighted_disabled(self, *, disabled: bool) -> None:
        """Set the disabled state for the highlighted server/connector or tool."""
        # In detail view, set the individual tool state, not the parent.
        if self._viewing_server is not None and self._viewing_kind is not None:
            self._set_highlighted_tool_disabled(disabled=disabled)
            return

        target = self._get_highlighted_target()
        if target is None:
            return
        name, kind = target

        if kind == MCPSourceKind.SERVER:
            for srv in self._mcp_servers:
                if srv.name == name:
                    srv.disabled = disabled
                    break
        else:
            cfg = self._find_connector_config(name)
            if cfg is None:
                cfg = ConnectorConfig(name=name, disabled=disabled)
                self._get_connector_configs().append(cfg)
            else:
                cfg.disabled = disabled

        self.post_message(self.MCPToggled(name=name, kind=kind, disabled=disabled))
        self._rebuild_preserving_scroll()

    def _set_highlighted_tool_disabled(self, *, disabled: bool) -> None:
        """Toggle a single tool inside a detail view."""
        server_name = self._viewing_server
        kind = self._viewing_kind
        if server_name is None or kind is None:
            return

        option_list = self.query_one(OptionList)
        highlighted = option_list.highlighted
        if highlighted is None:
            return
        option = option_list.get_option_at_index(highlighted)
        option_id = option.id or ""
        if not option_id.startswith("tool:"):
            return
        full_tool_name = option_id.removeprefix("tool:")

        # Look up the remote name from the index.
        tools_source = (
            self._index.connector_tools
            if kind == MCPSourceKind.CONNECTOR
            else self._index.server_tools
        )
        remote_name: str | None = None
        for t_name, cls in tools_source.get(server_name, []):
            if t_name == full_tool_name:
                remote_name = cls.get_remote_name()
                break
        if remote_name is None:
            return

        # Update disabled_tools on the config object.
        if kind == MCPSourceKind.SERVER:
            for srv in self._mcp_servers:
                if srv.name == server_name:
                    srv.disabled_tools = updated_tool_list(
                        srv.disabled_tools, remote_name, disabled
                    )
                    break
        else:
            cfg = self._find_connector_config(server_name)
            if cfg is None:
                cfg = ConnectorConfig(
                    name=server_name, disabled_tools=[remote_name] if disabled else []
                )
                self._get_connector_configs().append(cfg)
            else:
                cfg.disabled_tools = updated_tool_list(
                    cfg.disabled_tools, remote_name, disabled
                )

        self.post_message(
            self.MCPToggled(
                name=server_name, kind=kind, disabled=disabled, tool_name=remote_name
            )
        )
        self._rebuild_preserving_scroll()

    def _rebuild_preserving_scroll(self) -> None:
        """Rebuild the tool index and refresh the view, preserving highlight and scroll."""
        option_list = self.query_one(OptionList)
        saved_option_id: str | None = None
        if (idx := option_list.highlighted) is not None:
            saved_option_id = option_list.get_option_at_index(idx).id
        saved_scroll_y = option_list.scroll_offset.y

        self._index = collect_mcp_tool_index(
            self._mcp_servers, self._tool_manager, self._connector_names
        )
        self._refresh_view(self._viewing_server, kind=self._viewing_kind)

        if saved_option_id is not None:
            try:
                new_index = option_list.get_option_index(saved_option_id)
                option_list.highlighted = new_index
            except OptionDoesNotExist:
                pass
        option_list.scroll_to(
            y=saved_scroll_y, animate=False, force=True, immediate=True
        )

    def _get_highlighted_target(self) -> tuple[str, MCPSourceKind] | None:
        """Return (name, kind) for the currently highlighted option, or None."""
        # If we're inside a detail view, use the viewed server/connector.
        if self._viewing_server is not None and self._viewing_kind is not None:
            return self._viewing_server, self._viewing_kind

        option_list = self.query_one(OptionList)
        highlighted = option_list.highlighted
        if highlighted is None:
            return None
        option = option_list.get_option_at_index(highlighted)
        option_id = option.id or ""
        if option_id.startswith("server:"):
            return option_id.removeprefix("server:"), MCPSourceKind.SERVER
        if option_id.startswith("connector:"):
            return option_id.removeprefix("connector:"), MCPSourceKind.CONNECTOR
        return None

    # ── list view ────────────────────────────────────────────────────

    def _refresh_view(
        self, server_name: str | None, *, kind: MCPSourceKind | None = None
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
                kind = MCPSourceKind.SERVER
            else:
                kind = MCPSourceKind.CONNECTOR

        self._show_detail_view(server_name, option_list, index, kind=kind)

    def _show_list_view(self, option_list: OptionList, index: MCPToolIndex) -> None:
        self._viewing_server = None
        self._viewing_kind = None
        has_connectors = connectors_enabled() and bool(self._connector_names)
        title = "MCP Servers & Connectors" if has_connectors else "MCP Servers"
        self.query_one("#mcp-title", NoMarkupStatic).update(title)
        self._set_help_text(_LIST_VIEW_HELP_TOOLS)

        has_servers = bool(self._mcp_servers)

        if has_servers:
            self._list_mcp_servers(option_list, index)
        if has_connectors:
            if has_servers:
                option_list.add_option(Option(Text("", no_wrap=True), disabled=True))
            self._list_connectors(option_list=option_list, index=index)
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
                (i for i, opt in enumerate(option_list.options) if not opt.disabled), 0
            )
            option_list.highlighted = first_enabled

    def _list_mcp_servers(self, option_list: OptionList, index: MCPToolIndex) -> None:
        max_name = max(len(srv.name) for srv in self._mcp_servers)
        max_type = max(len(srv.transport) + 2 for srv in self._mcp_servers)
        option_list.add_option(
            Option(Text("Local MCP Servers", style="bold", no_wrap=True), disabled=True)
        )
        for srv in self._mcp_servers:
            tools = index.server_tools.get(srv.name, [])
            total = len(tools)
            enabled = sum(1 for t, _ in tools if t in index.enabled_tools)
            type_tag = f"[{srv.transport}]"
            label = Text(no_wrap=True)
            label.append(f"  {srv.name:<{max_name}}")
            label.append(f"  {type_tag:<{max_type}}", style="dim")
            label.append(f"  {_tool_count_text(enabled, total)}", style="dim")
            if srv.disabled:
                label.append("  ")
                label.append("○", style="dim")
                label.append(" disabled", style="dim")
            option_list.add_option(Option(label, id=f"server:{srv.name}"))

    def _list_connectors(self, option_list: OptionList, index: MCPToolIndex) -> None:
        ordered_connector_names = self._sorted_connector_names
        max_name = max(len(n) for n in ordered_connector_names)
        type_tag = "[connector]"
        type_width = len(type_tag)
        tool_texts: dict[str, str] = {}
        for n in ordered_connector_names:
            tools = index.connector_tools.get(n, [])
            total = len(tools)
            enabled = sum(1 for t, _ in tools if t in index.enabled_tools)
            tool_texts[n] = _tool_count_text(enabled, total)
        max_tools = max(len(t) for t in tool_texts.values())
        option_list.add_option(
            Option(
                Text("Workspace Connectors", style="bold", no_wrap=True), disabled=True
            )
        )
        for cname in ordered_connector_names:
            cfg = self._find_connector_config(cname)
            is_disabled = cfg.disabled if cfg else False
            connected = (
                self._connector_registry.is_connected(cname)
                if self._connector_registry
                else False
            )
            label = Text(no_wrap=True)
            label.append(f"  {cname:<{max_name}}")
            label.append(f"  {type_tag:<{type_width}}", style="dim")
            label.append(f"  {tool_texts[cname]:<{max_tools}}", style="dim")
            if is_disabled:
                label.append("  ")
                label.append("○", style="dim")
                label.append(" disabled", style="dim")
            elif connected:
                label.append("  ")
                label.append("●", style="green")
                label.append(" connected", style="dim")
            else:
                label.append("  ")
                label.append("○", style="dim")
                label.append(" not connected", style="dim")
            option_list.add_option(Option(label, id=f"connector:{cname}"))

    # ── detail view ──────────────────────────────────────────────────

    def _show_detail_view(
        self,
        server_name: str,
        option_list: OptionList,
        index: MCPToolIndex,
        *,
        kind: MCPSourceKind = MCPSourceKind.SERVER,
    ) -> None:
        self._viewing_server = server_name
        self._viewing_kind = kind
        is_connector = kind == MCPSourceKind.CONNECTOR
        title_prefix = "Connector" if is_connector else "MCP Server"
        self.query_one("#mcp-title", NoMarkupStatic).update(
            f"{title_prefix}: {server_name}"
        )
        self._set_help_text(_DETAIL_VIEW_HELP)
        tools_source = index.connector_tools if is_connector else index.server_tools
        all_tools = sorted(tools_source.get(server_name, []), key=lambda t: t[0])
        if not all_tools:
            if (
                is_connector
                and self._connector_registry
                and not self._connector_registry.is_connected(server_name)
            ):
                self.post_message(
                    self.ConnectorAuthRequested(
                        connector_name=server_name,
                        connector_registry=self._connector_registry,
                        tool_manager=self._tool_manager,
                    )
                )
            else:
                option_list.add_option(
                    Option("No tools discovered for this server", disabled=True)
                )
            return
        for tool_name, cls in all_tools:
            is_tool_enabled = tool_name in index.enabled_tools
            remote_name = cls.get_remote_name()
            raw_desc = (
                (cls.description or "").removeprefix(f"[{server_name}] ").split("\n")[0]
            )
            label = Text(no_wrap=True)
            if is_tool_enabled:
                label.append(remote_name, style="bold")
                if raw_desc:
                    label.append(f"  -  {raw_desc}")
            else:
                label.append(remote_name, style="dim")
                if raw_desc:
                    label.append(f"  -  {raw_desc}", style="dim")
                label.append("  (disabled)", style="dim italic")
            option_list.add_option(Option(label, id=f"tool:{tool_name}"))
        option_list.highlighted = 0


def _tool_count_text(enabled: int, total: int | None = None) -> str:
    if total is not None and enabled < total:
        noun = "tool" if total == 1 else "tools"
        return f"{enabled}/{total} {noun}"
    if enabled == 0:
        return "no tools"
    noun = "tool" if enabled == 1 else "tools"
    return f"{enabled} {noun}"


def _sort_connector_names_for_menu(
    connector_names: Sequence[str], connector_registry: ConnectorRegistry | None
) -> list[str]:
    return sorted(
        connector_names,
        key=lambda name: (
            not connector_registry.is_connected(name) if connector_registry else True,
            name.lower(),
        ),
    )
