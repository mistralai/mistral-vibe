from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.message import Message
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from vibe.app_server.models import (
    PluginCatalogComponent,
    PluginCatalogEntry,
    PluginCatalogState,
)
from vibe.app_server.resources import PluginCatalogChange, PluginCatalogDiff
from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.navigable_option_list import NavigableOptionList
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.vscode_compat import VscodeCompatInput

_LIST_VIEW_HELP = (
    f"{shortcut('↑↓/jk')} Navigate  {shortcut('Enter')} View  "
    f"{shortcut('/')} Search  {shortcut('r')} Reload  {shortcut('Esc')} Close"
)
_DETAIL_VIEW_HELP = (
    f"{shortcut('Backspace')} Back  {shortcut('r')} Reload  {shortcut('Esc')} Close"
)
_FILTER_HELP = f"{shortcut('Enter')} Apply  {shortcut('Esc')} Clear"
_PLUGIN_OPTION_PREFIX = "plugin:"
_DIGEST_WIDTH = 8
_UNKNOWN = "—"
_COMPONENT_LABELS = {
    "skill": "Skills",
    "knowledge": "Knowledge",
    "library": "Libraries",
    "mcp_server": "MCP servers",
    "connector": "Connectors",
    "hook": "Hooks",
    "agent": "Agents",
    "subagent": "Subagents",
    "tool": "Tools",
    "unknown": "Other",
}


class PluginsApp(Container):
    can_focus_children = True
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=False),
        Binding("backspace", "back", "Back", show=False),
        Binding("slash", "filter", "Search", show=False),
        Binding("r", "reload", "Reload", show=False),
    ]

    class PluginsClosed(Message):
        pass

    class PluginsReloadRequested(Message):
        pass

    def __init__(self, state: PluginCatalogState) -> None:
        super().__init__(id="plugins-app")
        self._state = state
        self._viewing_name: str | None = None
        self._query = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="plugins-content"):
            yield NoMarkupStatic("", id="plugins-title", classes="settings-title")
            yield NoMarkupStatic("")
            yield NavigableOptionList(id="plugins-options")
            yield VscodeCompatInput(
                placeholder="Filter plugins", id="plugins-filter", compact=True
            )
            yield NoMarkupStatic("", id="plugins-help", classes="settings-help")

    def on_mount(self) -> None:
        self._filter_input().display = False
        self._refresh_view()
        self.query_one(OptionList).focus()

    def update_state(self, state: PluginCatalogState) -> None:
        """Re-render from a catalogue a reload already returned."""
        self._state = state
        self._refresh_view()

    def action_close(self) -> None:
        if self._filtering:
            self._close_filter(clear=True)
            return
        self.post_message(self.PluginsClosed())

    def action_back(self) -> None:
        if self._viewing_name is None:
            return
        self._viewing_name = None
        self._refresh_view()

    def action_filter(self) -> None:
        if self._viewing_name is not None or self._filtering:
            return
        filter_input = self._filter_input()
        filter_input.display = True
        filter_input.focus()
        self._set_help_text(_FILTER_HELP)

    def action_reload(self) -> None:
        self.post_message(self.PluginsReloadRequested())

    def on_input_changed(self, event: Input.Changed) -> None:
        self._query = event.value.strip().casefold()
        self._refresh_view()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._close_filter(clear=False)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id or ""
        if not option_id.startswith(_PLUGIN_OPTION_PREFIX):
            return
        self._viewing_name = option_id.removeprefix(_PLUGIN_OPTION_PREFIX)
        self._refresh_view()

    @property
    def _filtering(self) -> bool:
        return self._filter_input().display

    def _filter_input(self) -> Input:
        return self.query_one("#plugins-filter", Input)

    def _close_filter(self, *, clear: bool) -> None:
        filter_input = self._filter_input()
        if clear:
            filter_input.value = ""
            self._query = ""
        filter_input.display = False
        self._refresh_view()
        self.query_one(OptionList).focus()

    def _viewing_entry(self) -> PluginCatalogEntry | None:
        return next(
            (
                entry
                for entry in self._state.plugins
                if entry.name == self._viewing_name
            ),
            None,
        )

    def _refresh_view(self) -> None:
        option_list = self.query_one(OptionList)
        option_list.clear_options()
        entry = self._viewing_entry()
        if entry is None:
            # A reload can take the plugin being viewed away.
            self._viewing_name = None
            self._show_list_view(option_list)
            return
        self._show_detail_view(option_list, entry)

    def _show_list_view(self, option_list: OptionList) -> None:
        plugins = self._state.plugins
        matching = [entry for entry in plugins if self._matches(entry)]
        self._set_title(f"Plugins · {len(plugins)} in this session")
        self._set_help_text(_FILTER_HELP if self._filtering else _LIST_VIEW_HELP)
        if not matching:
            option_list.add_option(
                Option(
                    "No plugins match this filter"
                    if self._query
                    else "No plugins in this session",
                    disabled=True,
                )
            )
        else:
            width = max(len(entry.name) for entry in matching)
            for entry in matching:
                option_list.add_option(
                    Option(
                        _entry_label(entry, width),
                        id=f"{_PLUGIN_OPTION_PREFIX}{entry.name}",
                    )
                )
        self._add_dropped(option_list)
        option_list.highlighted = next(
            (
                index
                for index, option in enumerate(option_list.options)
                if not option.disabled
            ),
            None,
        )

    def _add_dropped(self, option_list: OptionList) -> None:
        if not self._state.dropped:
            return
        option_list.add_option(Option(Text("", no_wrap=True), disabled=True))
        option_list.add_option(Option(Text("Not loaded", style="bold"), disabled=True))
        for dropped in self._state.dropped:
            option_list.add_option(
                Option(
                    Text(
                        f"  ! {_abbreviate_home(dropped.file)} — {dropped.message}",
                        style="dim",
                    ),
                    disabled=True,
                )
            )

    def _show_detail_view(
        self, option_list: OptionList, entry: PluginCatalogEntry
    ) -> None:
        self._set_title(entry.name)
        self._set_help_text(_DETAIL_VIEW_HELP)
        for line in _detail_lines(entry):
            option_list.add_option(Option(Text(line, style="dim"), disabled=True))

    def _matches(self, entry: PluginCatalogEntry) -> bool:
        if not self._query:
            return True
        return self._query in f"{entry.name} {entry.description}".casefold()

    def _set_title(self, text: str) -> None:
        self.query_one("#plugins-title", NoMarkupStatic).update(text)

    def _set_help_text(self, text: str) -> None:
        self.query_one("#plugins-help", NoMarkupStatic).update(shortcut_hint(text))


def plugin_reload_report(diff: PluginCatalogDiff) -> str:
    """What a reload moved, by digest, rather than that it ran."""
    if not diff.changes:
        return "Plugins reloaded. Nothing changed."
    versions = {entry.name: entry.version for entry in diff.state.plugins}
    return "\n".join((
        "### Plugins reloaded",
        "",
        *(_change_line(change, versions.get(change.name)) for change in diff.changes),
    ))


def _change_line(change: PluginCatalogChange, version: str | None) -> str:
    if change.before is None:
        return f"- `+` `{change.name}`{f' {version}' if version else ''}"
    if change.after is None:
        return f"- `-` `{change.name}` — no longer installed"
    before, after = _short_digest(change.before), _short_digest(change.after)
    return f"- `~` `{change.name}` {before} → {after}"


def _entry_label(entry: PluginCatalogEntry, width: int) -> Text:
    label = Text(no_wrap=True)
    label.append(f"  {entry.name:<{width}}")
    facts = " · ".join((
        entry.scope or _UNKNOWN,
        entry.source_format,
        _short_digest(entry.content_sha256),
    ))
    label.append(f"  {facts}", style="dim")
    if entry.drifted:
        label.append(f" · ⚠ {entry.drifted} drifted", style="yellow")
    if entry.installed_root is None:
        label.append(" · uninstalled since pin", style="dim")
    return label


def _detail_lines(entry: PluginCatalogEntry) -> Iterator[str]:
    yield f"  Scope: {entry.scope or _UNKNOWN}"
    yield f"  Format: {entry.source_format}"
    if entry.description:
        yield f"  {entry.description}"
    yield ""
    yield f"  Author: {entry.author or _UNKNOWN}"
    yield f"  Version: {entry.version or _UNKNOWN}"
    yield f"  Pinned: {_short_digest(entry.content_sha256)}"
    yield (
        "  Installed from: (uninstalled since pin)"
        if entry.installed_root is None
        else f"  Installed from: {_abbreviate_home(entry.installed_root)}"
    )
    if not entry.components:
        return
    yield ""
    yield "  Components:"
    yield from _component_lines(entry.components)


def _component_lines(components: Sequence[PluginCatalogComponent]) -> Iterator[str]:
    grouped: dict[str, list[str]] = {}
    for component in components:
        grouped.setdefault(component.kind, []).append(_component_name(component))
    for kind, names in grouped.items():
        yield f"  ● {_COMPONENT_LABELS[kind]}: {', '.join(names)}"


def _component_name(component: PluginCatalogComponent) -> str:
    if component.status is None:
        return component.name
    return f"{component.name} ({component.status})"


def _short_digest(digest: str | None) -> str:
    return _UNKNOWN if digest is None else digest[:_DIGEST_WIDTH]


def _abbreviate_home(path: str) -> str:
    home = str(Path.home())
    return f"~{path[len(home) :]}" if path.startswith(home) else path
