from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.events import DescendantBlur
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option, OptionDoesNotExist
from textual.worker import Worker

from vibe.app_server.models import (
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
)
from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.navigable_option_list import NavigableOptionList
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic

_LIST_VIEW_HELP_TOOLS = (
    f"{shortcut('↑↓/jk')} Navigate  {shortcut('Enter')} Show tools  "
    f"{shortcut('d')} Disable  {shortcut('e')} Enable  {shortcut('Esc')} Close"
)
_LIST_VIEW_HELP_AUTH = (
    f"{shortcut('↑↓/jk')} Navigate  {shortcut('Enter')} Connect  "
    f"{shortcut('d')} Disable  {shortcut('e')} Enable  {shortcut('Esc')} Close"
)
_DETAIL_VIEW_HELP = (
    f"{shortcut('↑↓/jk')} Navigate  {shortcut('d')} Disable  "
    f"{shortcut('e')} Enable  {shortcut('Backspace')} Back  {shortcut('Esc')} Close"
)
_DETAIL_VIEW_HELP_NO_TOOLS = (
    f"{shortcut('↑↓/jk')} Navigate  {shortcut('Backspace')} Back  "
    f"{shortcut('Esc')} Close"
)
_BACKGROUND_REFRESH_INTERVAL_SECONDS = 60.0


class MCPApp(Container):
    can_focus_children = True
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=False),
        Binding("backspace", "back", "Back", show=False),
        Binding("d", "disable", "Disable", show=False),
        Binding("e", "enable", "Enable", show=False),
    ]

    class MCPClosed(Message):
        pass

    class MCPToggled(Message):
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
        def __init__(self, connector_name: str) -> None:
            super().__init__()
            self.connector_name = connector_name

    class MCPOAuthRequested(Message):
        def __init__(self, server_name: str) -> None:
            super().__init__()
            self.server_name = server_name

    def __init__(
        self,
        state: MCPState,
        initial_source: str = "",
        state_getter: Callable[[], MCPState] | None = None,
        refresh_callback: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        super().__init__(id="mcp-app")
        self._state = state.model_copy(deep=True)
        self._state_getter = state_getter
        self._viewing_name: str | None = initial_source.strip() or None
        self._viewing_kind: MCPSourceKind | None = None
        self._refresh_callback = refresh_callback
        self._refreshing = False

    def compose(self) -> ComposeResult:
        with Vertical(id="mcp-content"):
            yield NoMarkupStatic("", id="mcp-title", classes="settings-title")
            yield NoMarkupStatic("")
            yield NavigableOptionList(id="mcp-options")
            yield NoMarkupStatic("", id="mcp-help", classes="settings-help")

    def on_mount(self) -> None:
        self._refresh_view(self._viewing_name)
        self.query_one(OptionList).focus()
        if self._refresh_callback is not None:
            self._start_refresh()
            self.set_interval(_BACKGROUND_REFRESH_INTERVAL_SECONDS, self._start_refresh)

    def refresh_index(self) -> None:
        if self._state_getter is not None:
            self._state = self._state_getter().model_copy(deep=True)
        self._rebuild_preserving_scroll()

    def on_descendant_blur(self, _event: DescendantBlur) -> None:
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        target = _source_from_option_id(event.option.id or "")
        if target is not None:
            name, kind = target
            self._refresh_view(name, kind=kind)

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        option_list = self.query_one(OptionList)
        highlighted = option_list.highlighted
        if (
            highlighted is not None
            and highlighted > 0
            and all(
                option_list.get_option_at_index(index).disabled
                for index in range(highlighted)
            )
        ):
            option_list.scroll_to(y=0, animate=False, force=True, immediate=True)
        if self._viewing_name is None:
            source = self._source_for_option(event.option)
            self._set_help_text(
                _LIST_VIEW_HELP_AUTH
                if source is not None and source.status is MCPSourceStatus.NEEDS_AUTH
                else _LIST_VIEW_HELP_TOOLS
            )

    def action_back(self) -> None:
        if self._viewing_name is not None:
            self._refresh_view(None)

    def action_close(self) -> None:
        self.post_message(self.MCPClosed())

    def action_disable(self) -> None:
        self._set_highlighted_disabled(disabled=True)

    def action_enable(self) -> None:
        self._set_highlighted_disabled(disabled=False)

    def _start_refresh(self) -> None:
        if self._refresh_callback is None or self._refreshing:
            return
        self._refreshing = True
        self.run_worker(self._run_refresh(), exclusive=True, group="refresh")

    async def _run_refresh(self) -> None:
        if self._refresh_callback is not None:
            await self._refresh_callback()

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.group != "refresh" or not event.worker.is_finished:
            return
        self._refreshing = False
        if self.is_attached:
            self.refresh_index()

    def _set_highlighted_disabled(self, *, disabled: bool) -> None:
        if self._viewing_name is not None and self._viewing_kind is not None:
            self._set_highlighted_tool_disabled(disabled=disabled)
            return
        target = self._highlighted_source()
        if target is None:
            return
        target.status = (
            MCPSourceStatus.DISABLED if disabled else MCPSourceStatus.ENABLED
        )
        self.post_message(
            self.MCPToggled(name=target.name, kind=target.kind, disabled=disabled)
        )
        self._rebuild_preserving_scroll()

    def _set_highlighted_tool_disabled(self, *, disabled: bool) -> None:
        source = self._viewing_source()
        option_list = self.query_one(OptionList)
        highlighted = option_list.highlighted
        if source is None or highlighted is None:
            return
        option_id = option_list.get_option_at_index(highlighted).id or ""
        if not option_id.startswith("tool:"):
            return
        tool_name = option_id.removeprefix("tool:")
        tool = next((tool for tool in source.tools if tool.name == tool_name), None)
        if tool is None:
            return
        tool.enabled = not disabled
        self.post_message(
            self.MCPToggled(
                name=source.name,
                kind=source.kind,
                disabled=disabled,
                tool_name=tool.name,
            )
        )
        self._rebuild_preserving_scroll()

    def _rebuild_preserving_scroll(self) -> None:
        option_list = self.query_one(OptionList)
        selected_id: str | None = None
        if (index := option_list.highlighted) is not None:
            selected_id = option_list.get_option_at_index(index).id
        scroll_y = option_list.scroll_offset.y
        self._refresh_view(self._viewing_name, kind=self._viewing_kind)
        if selected_id is not None:
            try:
                option_list.highlighted = option_list.get_option_index(selected_id)
            except OptionDoesNotExist:
                pass
        option_list.scroll_to(y=scroll_y, animate=False, force=True, immediate=True)

    def _refresh_view(
        self, name: str | None, *, kind: MCPSourceKind | None = None
    ) -> None:
        option_list = self.query_one(OptionList)
        option_list.clear_options()
        source = self._find_source(name, kind)
        if source is None:
            self._show_list_view(option_list)
            return
        self._show_detail_view(option_list, source)

    def _show_list_view(self, option_list: OptionList) -> None:
        self._viewing_name = None
        self._viewing_kind = None
        servers = _sort_sources_for_menu(self._sources(MCPSourceKind.SERVER))
        connectors = _sort_sources_for_menu(self._sources(MCPSourceKind.CONNECTOR))
        self.query_one("#mcp-title", NoMarkupStatic).update(
            "MCP Servers & Connectors" if connectors else "MCP Servers"
        )
        self._set_help_text(_LIST_VIEW_HELP_TOOLS)
        if servers:
            self._add_source_group(option_list, "Local MCP Servers", servers)
        if connectors:
            if servers:
                option_list.add_option(Option(Text("", no_wrap=True), disabled=True))
            self._add_source_group(option_list, "Workspace Connectors", connectors)
        if not servers and not connectors:
            option_list.add_option(
                Option("No MCP servers or connectors configured", disabled=True)
            )
            return
        option_list.highlighted = next(
            (
                index
                for index, option in enumerate(option_list.options)
                if not option.disabled
            ),
            0,
        )

    def _add_source_group(
        self, option_list: OptionList, title: str, sources: Sequence[MCPSourceSummary]
    ) -> None:
        option_list.add_option(Option(Text(title, style="bold"), disabled=True))
        max_name = max(len(source.name) for source in sources)
        max_transport = max(len(source.transport) + 2 for source in sources)
        tool_labels = {}
        for source in sources:
            enabled = sum(tool.enabled for tool in source.tools)
            total = len(source.tools)
            if (
                source.kind is MCPSourceKind.SERVER
                and source.status is MCPSourceStatus.UNAVAILABLE
                and total == 0
            ):
                tool_labels[source.name] = "tool discovery failed"
            else:
                tool_labels[source.name] = _tool_count_text(enabled, total)
        max_tools = max(len(label) for label in tool_labels.values())
        for source in sources:
            label = Text(no_wrap=True)
            type_tag = f"[{source.transport}]"
            label.append(f"  {source.name:<{max_name}}")
            label.append(f"  {type_tag:<{max_transport}}", style="dim")
            label.append(f"  {tool_labels[source.name]:<{max_tools}}", style="dim")
            symbol, style, status = _source_status(source)
            _append_status(label, symbol, style, status)
            option_list.add_option(
                Option(label, id=_source_option_id(source.name, source.kind))
            )

    def _show_detail_view(
        self, option_list: OptionList, source: MCPSourceSummary
    ) -> None:
        self._viewing_name = source.name
        self._viewing_kind = source.kind
        prefix = "Connector" if source.kind is MCPSourceKind.CONNECTOR else "MCP Server"
        self.query_one("#mcp-title", NoMarkupStatic).update(f"{prefix}: {source.name}")
        if source.status is MCPSourceStatus.NEEDS_AUTH:
            self._set_help_text(_DETAIL_VIEW_HELP_NO_TOOLS)
            if source.kind is MCPSourceKind.CONNECTOR:
                self.post_message(self.ConnectorAuthRequested(source.name))
            else:
                self.post_message(self.MCPOAuthRequested(source.name))
            return
        if source.status is MCPSourceStatus.NEEDS_SETUP:
            self._set_help_text(_DETAIL_VIEW_HELP_NO_TOOLS)
            option_list.add_option(
                Option(
                    shortcut_hint(
                        "Set up credentials in the Mistral dashboard, then press "
                        f"{shortcut('r')} to refresh."
                    ),
                    disabled=True,
                )
            )
            return
        self._set_help_text(
            _DETAIL_VIEW_HELP if source.tools else _DETAIL_VIEW_HELP_NO_TOOLS
        )
        if not source.tools:
            if (
                source.kind is MCPSourceKind.SERVER
                and source.status is MCPSourceStatus.UNAVAILABLE
            ):
                option_list.add_option(Option("Tool discovery failed", disabled=True))
                if error := self._state.discovery_errors.get(source.name):
                    option_list.add_option(
                        Option(Text(error, style="dim"), disabled=True)
                    )
            else:
                option_list.add_option(Option("No tools discovered", disabled=True))
            return
        for tool in sorted(source.tools, key=lambda item: item.name):
            label = Text(no_wrap=True)
            style = "bold" if tool.enabled else "dim"
            label.append(tool.name, style=style)
            if tool.description:
                label.append(
                    f"  -  {tool.description}", style=None if tool.enabled else "dim"
                )
            if not tool.enabled:
                label.append("  (disabled)", style="dim italic")
            option_list.add_option(Option(label, id=f"tool:{tool.name}"))
        option_list.highlighted = 0

    def _sources(self, kind: MCPSourceKind) -> list[MCPSourceSummary]:
        return [source for source in self._state.sources if source.kind is kind]

    def _find_source(
        self, name: str | None, kind: MCPSourceKind | None
    ) -> MCPSourceSummary | None:
        if name is None:
            return None
        candidates = [source for source in self._state.sources if source.name == name]
        if kind is not None:
            return next((source for source in candidates if source.kind is kind), None)
        return next(
            (source for source in candidates if source.kind is MCPSourceKind.SERVER),
            candidates[0] if candidates else None,
        )

    def _viewing_source(self) -> MCPSourceSummary | None:
        return self._find_source(self._viewing_name, self._viewing_kind)

    def _highlighted_source(self) -> MCPSourceSummary | None:
        option_list = self.query_one(OptionList)
        highlighted = option_list.highlighted
        if highlighted is None:
            return None
        return self._source_for_option(option_list.get_option_at_index(highlighted))

    def _source_for_option(self, option: Option) -> MCPSourceSummary | None:
        target = _source_from_option_id(option.id or "")
        return self._find_source(*target) if target is not None else None

    def _set_help_text(self, text: str) -> None:
        self.query_one("#mcp-help", NoMarkupStatic).update(shortcut_hint(text))


def _source_option_id(name: str, kind: MCPSourceKind) -> str:
    return f"{kind.value}:{name}"


def _source_from_option_id(value: str) -> tuple[str, MCPSourceKind] | None:
    for kind in MCPSourceKind:
        prefix = f"{kind.value}:"
        if value.startswith(prefix):
            return value.removeprefix(prefix), kind
    return None


def _source_status(source: MCPSourceSummary) -> tuple[str, str, str]:
    match source.status:
        case MCPSourceStatus.CONNECTED:
            return "●", "green", "connected"
        case MCPSourceStatus.ENABLED:
            return "●", "green", "enabled"
        case MCPSourceStatus.NEEDS_AUTH:
            return "○", "dim", "needs auth"
        case MCPSourceStatus.NEEDS_SETUP:
            return "○", "dim", "needs setup"
        case MCPSourceStatus.UNAVAILABLE:
            hint = (
                "check your config"
                if source.kind is MCPSourceKind.SERVER
                else "try refreshing"
            )
            return "○", "dim", f"error - {hint}"
        case MCPSourceStatus.DISABLED:
            return "○", "dim", "disabled"


def _append_status(label: Text, symbol: str, symbol_style: str, text: str) -> None:
    label.append("  ")
    label.append(symbol, style=symbol_style)
    label.append(f" {text}", style="dim")


def _tool_count_text(enabled: int, total: int) -> str:
    if enabled < total:
        return f"{enabled}/{total} {'tool' if total == 1 else 'tools'}"
    if enabled == 0:
        return "no tools"
    return f"{enabled} {'tool' if enabled == 1 else 'tools'}"


def _sort_sources_for_menu(
    sources: Sequence[MCPSourceSummary],
) -> list[MCPSourceSummary]:
    return sorted(
        sources,
        key=lambda source: (not source.tools, source.name.casefold(), source.name),
    )
