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
from vibe.core.tools.mcp.tools import MCPTool

if TYPE_CHECKING:
    from vibe.core.config import MCPServer
    from vibe.core.tools.manager import ToolManager


class MCPToolIndex(NamedTuple):
    server_tools: dict[str, list[tuple[str, type[MCPTool]]]]
    enabled_tools: dict[str, type[Any]]


def collect_mcp_tool_index(
    mcp_servers: Sequence[MCPServer], tool_manager: ToolManager
) -> MCPToolIndex:
    registered = tool_manager.registered_tools
    available = tool_manager.available_tools
    configured_servers = {server.name for server in mcp_servers}
    server_tools: dict[str, list[tuple[str, type[MCPTool]]]] = {}

    for tool_name, cls in registered.items():
        if not issubclass(cls, MCPTool):
            continue
        server_name = cls.get_server_name()
        if server_name is None or server_name not in configured_servers:
            continue
        server_tools.setdefault(server_name, []).append((tool_name, cls))

    return MCPToolIndex(server_tools, enabled_tools=available)


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
    ) -> None:
        super().__init__(id="mcp-app")
        self._mcp_servers = mcp_servers
        self._tool_manager = tool_manager
        self._index = collect_mcp_tool_index(mcp_servers, tool_manager)
        self._viewing_server: str | None = initial_server.strip() or None

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

    def on_descendant_blur(self, _event: DescendantBlur) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id or ""
        if option_id.startswith("server:"):
            self._refresh_view(option_id.removeprefix("server:"))

    def action_back(self) -> None:
        if self._viewing_server is not None:
            self._refresh_view(None)

    def action_close(self) -> None:
        self.post_message(self.MCPClosed())

    def _refresh_view(self, server_name: str | None) -> None:
        index = self._index
        option_list = self.query_one(OptionList)
        option_list.clear_options()

        server_names = {s.name for s in self._mcp_servers}
        if server_name is None or server_name not in server_names:
            self._viewing_server = None
            self.query_one("#mcp-title", NoMarkupStatic).update("MCP Servers")
            self.query_one("#mcp-help", NoMarkupStatic).update(
                "↑↓ Navigate  Enter Show tools  Esc Close"
            )
            for srv in self._mcp_servers:
                tools = index.server_tools.get(srv.name, [])
                enabled = sum(1 for t, _ in tools if t in index.enabled_tools)
                status = _server_status(enabled)
                label = Text(no_wrap=True)
                label.append(srv.name)
                label.append(f"  [{srv.transport}]  {status}")
                option_list.add_option(Option(label, id=f"server:{srv.name}"))
            if self._mcp_servers:
                option_list.highlighted = 0
            return

        self._viewing_server = server_name
        self.query_one("#mcp-title", NoMarkupStatic).update(
            f"MCP Server: {server_name}"
        )
        self.query_one("#mcp-help", NoMarkupStatic).update(
            "↑↓ Navigate  Backspace Back  Esc Close"
        )
        enabled_tools = [
            (tool_name, cls)
            for tool_name, cls in sorted(
                index.server_tools.get(server_name, []), key=lambda t: t[0]
            )
            if tool_name in index.enabled_tools
        ]
        if not enabled_tools:
            option_list.add_option(
                Option("No enabled tools for this server", disabled=True)
            )
            return
        for tool_name, cls in enabled_tools:
            remote_name = cls.get_remote_name()
            raw_desc = (
                (cls.description or "").removeprefix(f"[{server_name}] ").split("\n")[0]
            )
            label = Text(no_wrap=True)
            label.append(remote_name, style="bold")
            if raw_desc:
                label.append(f"  -  {raw_desc}")
            option_list.add_option(Option(label, id=f"tool:{tool_name}"))
        if enabled_tools:
            option_list.highlighted = 0


def _server_status(enabled: int) -> str:
    if enabled == 0:
        return "unavailable"
    noun = "tool" if enabled == 1 else "tools"
    return f"{enabled} {noun} enabled"
