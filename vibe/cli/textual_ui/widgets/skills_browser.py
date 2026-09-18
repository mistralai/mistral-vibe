"""Bottom-app browser for the skills available to a session.

Two panes, styled like the MCP browser: a navigable option list on the left
(installed pins + importable catalog, or a skill's versions) and a live preview
of the highlighted skill on the right. Speaks only app-server view models, so
the Textual layer stays free of ``vibe.core`` and drives every registry
operation through the app-server skills resource.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Protocol

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.events import DescendantBlur, Key
from textual.message import Message
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from vibe.app_server.models import SkillCatalogEntry, SkillSummary, SkillVersionView
from vibe.app_server.protocol import SkillsDetailResponse
from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.navigable_option_list import NavigableOptionList
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.vscode_compat import VscodeCompatInput
from vibe.observability.logging import logger

REGISTRY_LATEST_ALIAS = "latest"

_TABS: tuple[tuple[str, str], ...] = (
    ("tab-installed", "Installed"),
    ("tab-available", "Available"),
)

_LIST_HELP = (
    f"{shortcut('↑↓/jk')} Navigate  {shortcut('←→')} Tabs  {shortcut('/')} Search  "
    f"{shortcut('Enter')} Select  {shortcut('t')} On/off  {shortcut('x')} Remove  "
    f"{shortcut('Esc')} Close"
)
_STEP_HELP = (
    f"{shortcut('↑↓/jk')} Navigate  {shortcut('Enter')} Choose  "
    f"{shortcut('Backspace')} Back  {shortcut('Esc')} Close"
)
_SEARCH_HELP = (
    f"Type to filter  {shortcut('↑↓')} Navigate  {shortcut('Enter')} Select  "
    f"{shortcut('Esc')} Done"
)


class _BrowserOptionList(NavigableOptionList):
    """Option list that hands focus back to the search row when you go up past
    the first row, so the search bar behaves like the top of the list.
    """

    def __init__(
        self, *args: Any, on_up_at_top: Callable[[], None], **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._on_up_at_top = on_up_at_top

    def _at_top(self) -> bool:
        first = next(
            (i for i, option in enumerate(self.options) if not option.disabled), None
        )
        return first is None or self.highlighted is None or self.highlighted <= first

    def on_key(self, event: Key) -> None:
        if event.key in {"up", "k"} and self._at_top():
            self._on_up_at_top()
            event.stop()
            event.prevent_default()


class _SearchInput(VscodeCompatInput):
    """Search row above the list. Down or Enter drops focus into the list, so
    typing filters here and row actions live one step down.
    """

    def __init__(
        self, *args: Any, on_leave_down: Callable[[], None], **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self._on_leave_down = on_leave_down

    def on_key(self, event: Key) -> None:
        if event.key in {"down", "enter"}:
            self._on_leave_down()
            event.stop()
            event.prevent_default()
        elif event.key == "ctrl+c" and self.value:
            self.value = ""
            event.stop()
            event.prevent_default()


@dataclass
class _VersionTarget:
    """The skill whose versions are being browsed (installed pin or catalog)."""

    name: str
    skill_id: str
    scope: str
    current: int | None
    installed: bool
    alias: str | None = None


@dataclass
class _ImportTarget:
    """A catalog skill awaiting a scope choice before it is imported."""

    skill_id: str
    name: str
    version: int | None = None
    alias: str | None = None


InstalledRefresh = Callable[[], Awaitable[Sequence[SkillSummary]]]


class SkillsActions(Protocol):
    """Registry operations, satisfied structurally by the app-server resource."""

    async def detail(
        self, skill_id: str, *, version: int | None = None
    ) -> SkillsDetailResponse: ...

    async def versions(self, skill_id: str) -> list[SkillVersionView]: ...

    async def import_skill(
        self,
        skill_id: str,
        *,
        version: int | None = None,
        alias: str | None = None,
        scope: str = "global",
    ) -> list[SkillSummary]: ...

    async def set_version(
        self, name: str, version: int, scope: str
    ) -> list[SkillSummary]: ...

    async def set_latest(self, name: str, scope: str) -> list[SkillSummary]: ...

    async def set_alias(
        self, name: str, alias: str, scope: str
    ) -> list[SkillSummary]: ...

    async def remove(self, name: str, scope: str) -> list[SkillSummary]: ...

    async def set_enabled(self, name: str, enabled: bool) -> list[SkillSummary]: ...

    async def read_installed(self) -> list[SkillSummary]: ...


def _row_key(info: SkillSummary) -> str:
    """Identity of an installed row, stable across a list that reorders.

    Restoring the cursor by position lands it on whatever now sits at that
    index, so a remove followed by another remove hits a skill the user never
    highlighted.
    """
    return f"{info.name}|{info.scope}|{info.source}"


def _resolution_rank(info: SkillSummary) -> tuple[int, int]:
    """How the agent picks between same-named skills, in discovery's own order.

    Source decides first: discovery seeds builtins, adds local files, and only
    then takes a registry pin whose name is still free, so a global local skill
    beats a project registry pin. Scope only breaks ties within a source, where
    the project search path is walked before the global one.
    """
    return (
        0 if info.source in {"builtin", "local", "plugin"} else 1,
        0 if info.scope == "project" else 1,
    )


def _pin_label(info: SkillSummary) -> str:
    if info.source != "registry" or info.registry is None:
        return ""
    reg = info.registry
    if reg.alias:
        return f"v{reg.version} ({reg.alias})"
    return f"v{reg.version}"


class SkillsBrowserApp(Container):
    """Browse, import, pin, and remove skills."""

    can_focus_children = True
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=False),
        Binding("backspace", "back", "Back", show=False),
        Binding("v", "versions", "Versions", show=False),
        Binding("x", "remove", "Remove", show=False),
        Binding("t", "toggle_enabled", "On/off", show=False),
        Binding("slash", "search", "Search", show=False),
        Binding("right", "next_tab", "Next tab", show=False),
        Binding("left", "prev_tab", "Previous tab", show=False),
    ]

    class Closed(Message):
        pass

    def __init__(
        self,
        actions: SkillsActions,
        installed: Sequence[SkillSummary],
        catalog: Sequence[SkillCatalogEntry],
        updates: dict[str, int],
        on_changed: InstalledRefresh,
        project_available: bool = False,
        catalog_loaded: bool = False,
        authenticated: bool = True,
    ) -> None:
        super().__init__(id="skillsbrowser-app")
        self._actions = actions
        self._installed: list[SkillSummary] = list(installed)
        self._catalog: list[SkillCatalogEntry] = list(catalog)
        self._updates = dict(updates)
        self._on_changed = on_changed
        self._project_available = project_available
        self._catalog_loaded = catalog_loaded
        self._authenticated = authenticated
        self._version_view: _VersionTarget | None = None
        self._versions: list[SkillVersionView] = []
        self._scope_target: _ImportTarget | None = None
        self._alias_targets: dict[str, int] = {}
        self._body_cache: dict[tuple[str, int | None], str] = {}
        self._busy = False
        self._query = ""
        self._tab = _TABS[0][0]

    def compose(self) -> ComposeResult:
        with Vertical(id="skillsbrowser-content"):
            yield NoMarkupStatic("", id="skillsbrowser-title", classes="settings-title")
            yield NoMarkupStatic("", id="skillsbrowser-tabs")
            yield _SearchInput(
                placeholder="Filter skills…",
                id="skillsbrowser-search",
                select_on_focus=False,
                on_leave_down=self._focus_list,
            )
            with Horizontal(id="skillsbrowser-body"):
                yield _BrowserOptionList(
                    id="skillsbrowser-options", on_up_at_top=self._focus_search
                )
                with VerticalScroll(id="skillsbrowser-preview"):
                    yield NoMarkupStatic("", id="skillsbrowser-preview-body")
            yield NoMarkupStatic("", id="skillsbrowser-help", classes="settings-help")

    def on_mount(self) -> None:
        if not self._installed:
            self._tab = "tab-available"
        self._render_tabs()
        self._show_list()
        self.query_one(OptionList).focus()

    @property
    def _searching(self) -> bool:
        """Is the search box focused, however the user got there?"""
        found = self.query("#skillsbrowser-search")
        return bool(found) and found.first(Input).has_focus

    def on_descendant_blur(self, _event: DescendantBlur) -> None:
        if self._searching:
            return
        self.query_one(OptionList).focus()

    def _render_tabs(self) -> None:
        bar = Text(no_wrap=True)
        for index, (tab_id, label) in enumerate(_TABS):
            if index:
                bar.append("  ")
            if tab_id == self._tab:
                bar.append(f" {label} ", style="bold reverse")
            else:
                bar.append(f" {label} ", style="dim")
        self.query_one("#skillsbrowser-tabs", NoMarkupStatic).update(bar)

    def _cycle_tab(self, step: int) -> None:
        if (
            self._version_view is not None
            or self._scope_target is not None
            or self._searching
        ):
            return
        ids = [tab_id for tab_id, _ in _TABS]
        current = ids.index(self._tab) if self._tab in ids else 0
        self._tab = ids[(current + step) % len(ids)]
        self._render_tabs()
        self._show_list()
        self.query_one(OptionList).focus()

    def action_next_tab(self) -> None:
        self._cycle_tab(1)

    def action_prev_tab(self) -> None:
        self._cycle_tab(-1)

    def action_search(self) -> None:
        self._focus_search()

    def _focus_search(self) -> None:
        if self._version_view is not None or self._scope_target is not None:
            return
        self.query_one("#skillsbrowser-search", Input).focus()
        self._set_help(_SEARCH_HELP)

    def _focus_list(self) -> None:
        option_list = self.query_one(OptionList)
        first = next(
            (i for i, opt in enumerate(option_list.options) if not opt.disabled), None
        )
        if first is None:
            return
        option_list.highlighted = first
        option_list.focus()
        self._apply_tab_help()

    def try_escape_search(self) -> bool:
        """Leave the search row for the list if it is focused, keeping the filter.

        Returns whether the escape was consumed, so the app's global Esc handler
        can skip closing the whole browser when the user is only leaving search.
        """
        if not self.query_one("#skillsbrowser-search", Input).has_focus:
            return False
        self._focus_list()
        return True

    def on_key(self, event: Key) -> None:
        if event.key == "escape" and self.try_escape_search():
            event.stop()
            event.prevent_default()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "skillsbrowser-search":
            return
        self._query = event.value
        if self._version_view is None and self._scope_target is None:
            self._show_list()
            self._set_help(_SEARCH_HELP)

    def _set_search_visible(self, visible: bool) -> None:
        self.query_one("#skillsbrowser-search", Input).display = visible

    def _set_preview_visible(self, visible: bool) -> None:
        self.query_one("#skillsbrowser-preview", VerticalScroll).display = visible

    def _matches(self, *fields: str | None) -> bool:
        if not self._query:
            return True
        needle = self._query.casefold()
        return any(field and needle in field.casefold() for field in fields)

    def _apply_tab_help(self) -> None:
        self._set_help(_LIST_HELP)

    def on_option_list_option_highlighted(
        self, _event: OptionList.OptionHighlighted
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
        self._update_preview()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._activate_option(event.option.id or "")

    def _activate_option(self, option_id: str) -> None:
        if option_id.startswith("scope:"):
            self._run(self._do_import(option_id.removeprefix("scope:")))
        elif option_id.startswith("alias:"):
            self._run(self._pin_alias(option_id.removeprefix("alias:")))
        elif option_id.startswith("version:"):
            self._run(self._pin_version(int(option_id.removeprefix("version:"))))
        elif option_id.startswith(("catalog:", "installed:")):
            self.action_versions()

    def action_back(self) -> None:
        if self._scope_target is not None:
            self._scope_target = None
            if self._version_view is not None:
                self._show_versions()
            else:
                self._show_list()
            return
        if self._version_view is not None:
            self._version_view = None
            self._versions = []
            self._show_list()

    def action_close(self) -> None:
        self.post_message(self.Closed())

    def action_versions(self) -> None:
        if self._version_view is not None or self._scope_target is not None:
            return
        target = self._version_target()
        if target is not None:
            self._run(self._open_versions(target))

    def action_remove(self) -> None:
        if self._version_view is not None or self._scope_target is not None:
            return
        info = self._highlighted_installed()
        if info is not None and info.source == "registry":
            self._run(self._remove(info.name, info.scope))

    def action_toggle_enabled(self) -> None:
        if self._version_view is not None or self._scope_target is not None:
            return
        info = self._highlighted_installed()
        if info is None:
            return
        if info.locked:
            self.notify(
                f"'{info.name}' is set by a config pattern; edit enabled_skills"
                " or disabled_skills to change it.",
                severity="warning",
                markup=False,
            )
            return
        self._run(self._toggle_enabled(info.name, not info.enabled))

    def _version_target(self) -> _VersionTarget | None:
        """The registry skill under the cursor (installed pin or catalog entry)."""
        option_id = self._highlighted_id()
        if option_id.startswith("installed:"):
            info = self._highlighted_installed()
            if info is None or info.source != "registry" or info.registry is None:
                return None
            return _VersionTarget(
                name=info.name,
                skill_id=info.registry.skill_id,
                scope=info.scope,
                current=info.registry.version,
                installed=True,
                alias=info.registry.alias,
            )
        if option_id.startswith("catalog:"):
            skill_id = option_id.removeprefix("catalog:")
            entry = next((c for c in self._catalog if c.skill_id == skill_id), None)
            if entry is None:
                return None
            return _VersionTarget(entry.name, skill_id, "global", None, False)
        return None

    def _has_update(self, info: SkillSummary) -> bool:
        if (
            info.source != "registry"
            or info.registry is None
            or info.registry.alias is not None
        ):
            return False
        latest = self._updates.get(info.name)
        return latest is not None and latest > info.registry.version

    def _show_list(self) -> None:
        option_list = self.query_one(OptionList)
        prev_id = self._highlighted_id()
        prev_index = option_list.highlighted
        option_list.clear_options()
        self.query_one("#skillsbrowser-title", NoMarkupStatic).update("Skills")
        self._set_search_visible(True)
        if not self._searching:
            self._apply_tab_help()

        blocking = [
            i
            for i in self._installed
            if i.scope == "project" or not self._project_available
        ]
        installed_names = {i.name for i in blocking}
        installed_ids = {
            i.registry.skill_id for i in blocking if i.registry is not None
        }
        installed = [
            (_row_key(info), info)
            for info in self._display_rows()
            if self._matches(info.name, info.description)
        ]
        importable = [
            c
            for c in self._catalog
            if c.name not in installed_names
            and c.skill_id not in installed_ids
            and self._matches(c.name, c.description)
        ]
        width = max(
            max((len(info.name) for _, info in installed), default=0),
            max((len(c.name) for c in importable), default=0),
        )

        if self._tab == "tab-available":
            ordered = sorted(importable, key=lambda c: c.name.casefold())
            for entry in ordered:
                option_list.add_option(
                    Option(
                        self._catalog_row(entry, width), id=f"catalog:{entry.skill_id}"
                    )
                )
            self._finish_list(
                option_list,
                bool(ordered),
                self._empty_message(),
                prefer=prev_id,
                prefer_index=prev_index if prev_id.startswith("catalog:") else None,
            )
            return

        fields = [self._installed_fields(info) for _, info in installed]
        scope_w = max((len(f[0]) for f in fields), default=0)
        source_w = max((len(f[1]) for f in fields), default=0)
        version_w = max((len(f[2]) for f in fields), default=0)
        for (key, info), cols in zip(installed, fields, strict=True):
            option_list.add_option(
                Option(
                    self._installed_row(
                        info, width, cols, scope_w, source_w, version_w
                    ),
                    id=f"installed:{key}",
                )
            )
        self._finish_list(
            option_list,
            bool(installed),
            Text("No matching skills" if self._query else "No skills installed"),
            prefer=prev_id,
            prefer_index=prev_index if prev_id.startswith("installed:") else None,
        )

    def _finish_list(
        self,
        option_list: OptionList,
        has_rows: bool,
        empty: Text,
        prefer: str = "",
        prefer_index: int | None = None,
    ) -> None:
        """Render the list and put the cursor back where the user left it.

        The same skill if it is still listed, else the position it occupied, so
        removing the highlighted row leaves the cursor on its neighbour instead
        of jumping to the top. Matching on position alone is what let the cursor
        sit on a different skill than the one it appeared to be on.
        """
        if not has_rows:
            option_list.add_option(Option(empty, disabled=True))
            self.query_one("#skillsbrowser-preview-body", NoMarkupStatic).update("")
            self._set_preview_visible(False)
            return
        self._set_preview_visible(True)
        selectable = [
            i for i, opt in enumerate(option_list.options) if not opt.disabled
        ]
        keep = next(
            (
                i
                for i, opt in enumerate(option_list.options)
                if prefer and opt.id == prefer and not opt.disabled
            ),
            None,
        )
        if keep is None and prefer_index is not None and selectable:
            keep = min(selectable, key=lambda i: (abs(i - prefer_index), i))
        option_list.highlighted = (
            keep if keep is not None else next(iter(selectable), 0)
        )
        self._update_preview()

    def _empty_message(self) -> Text:
        """The empty-list row, as Text so a query like ``[/]`` is never parsed."""
        if self._query:
            return Text(f"No skills match '{self._query}'")
        if not self._authenticated:
            return Text("Sign in to Mistral to browse shared skills")
        if not self._catalog_loaded:
            return Text("Could not load the shared skills catalog")
        return Text("No skills available")

    def _installed_fields(self, info: SkillSummary) -> tuple[str, str, str]:
        """The three metadata columns for an installed row: scope, source, version.

        Enum-like values (scope, source) get a clean capitalized display; the
        version leads with the concrete number and, if it tracks an alias, shows
        that ref in parentheses (e.g. ``v5 (latest)``).
        """
        scope = info.scope.capitalize() if info.source in {"local", "registry"} else ""
        if info.source == "registry" and info.registry is not None:
            reg = info.registry
            version = (
                f"v{reg.version} ({reg.alias})" if reg.alias else f"v{reg.version}"
            )
        else:
            version = ""
        return scope, info.source.capitalize(), version

    def _installed_row(
        self,
        info: SkillSummary,
        width: int,
        cols: tuple[str, str, str],
        scope_w: int,
        source_w: int,
        version_w: int,
    ) -> Text:
        scope, source, version = cols
        row = Text(no_wrap=True)
        glyph = "●" if info.enabled else "○"
        if info.locked:
            style = "yellow" if info.enabled else "yellow dim"
        else:
            style = "green" if info.enabled else "dim"
        row.append(f"  {glyph} ", style=style)
        row.append(f"{info.name:<{width}}")
        row.append("   ")
        row.append(f"{scope:<{scope_w}}", style="dim")
        row.append("  ")
        row.append(f"{source:<{source_w}}", style="dim")
        row.append("  ")
        row.append(f"{version:<{version_w}}", style="dim")
        if self._has_update(info):
            row.append("  ")
            row.append("●", style="blue")
            row.append(f" New v{self._updates[info.name]}", style="blue")
        return row

    def _catalog_row(self, entry: SkillCatalogEntry, width: int) -> Text:
        badges = ["Shared"]
        if entry.sharing_scope in {"private", "workspace"}:
            badges.append(entry.sharing_scope.capitalize())
        badges.append(f"v{entry.latest_version}")
        row = Text(no_wrap=True)
        row.append(f"  {entry.name:<{width}}")
        row.append(f"  [{' · '.join(badges)}]", style="dim")
        return row

    def _display_rows(self) -> list[SkillSummary]:
        """One row per name for the list, the one the agent would resolve to.

        ``self._installed`` carries every pin (a skill can be pinned globally
        and in the project, and a local file can shadow a registry pin). The
        agent resolves a name to exactly one skill, so the list shows one row
        and the detail page manages the rest.
        """
        best: dict[str, SkillSummary] = {}
        for info in self._installed:
            current = best.get(info.name)
            if current is None or _resolution_rank(info) < _resolution_rank(current):
                best[info.name] = info
        return list(best.values())

    def _highlighted_installed(self) -> SkillSummary | None:
        option_id = self._highlighted_id()
        if not option_id.startswith("installed:"):
            return None
        key = option_id.removeprefix("installed:")
        return next((i for i in self._installed if _row_key(i) == key), None)

    def _highlighted_id(self) -> str:
        option_list = self.query_one(OptionList)
        highlighted = option_list.highlighted
        if highlighted is None:
            return ""
        return option_list.get_option_at_index(highlighted).id or ""

    async def _open_versions(self, target: _VersionTarget) -> None:
        self.query_one(OptionList).focus()
        self._set_status(f"Loading versions for {target.name}…")
        self._versions = await self._actions.versions(target.skill_id)
        self._version_view = target
        self._show_versions()

    def _show_versions(self) -> None:
        target = self._version_view
        if target is None:
            return
        self._alias_targets = {
            alias: v.version
            for v in self._versions
            for alias in v.aliases
            if alias != REGISTRY_LATEST_ALIAS
        }
        option_list = self.query_one(OptionList)
        option_list.clear_options()
        title = (
            f"{target.name}: versions"
            if target.installed
            else f"Import {target.name}: choose a version"
        )
        self.query_one("#skillsbrowser-title", NoMarkupStatic).update(title)
        self._set_search_visible(False)
        self._set_preview_visible(True)
        self._set_help(_STEP_HELP)
        newest = max((v.version for v in self._versions), default=None)

        option_list.add_option(Option(Text("Aliases", style="bold"), disabled=True))
        latest = Text(no_wrap=True)
        is_latest = target.alias == REGISTRY_LATEST_ALIAS
        latest.append(f"  {'›' if is_latest else ' '} latest")
        if newest is not None:
            latest.append(f" → v{newest}", style="dim")
        latest.append("  always newest", style="italic dim")
        if is_latest:
            latest.append("  (current)", style="dim")
        option_list.add_option(Option(latest, id="alias:latest"))
        for alias, version in sorted(self._alias_targets.items()):
            line = Text(no_wrap=True)
            current_alias = target.alias == alias
            line.append(f"  {'›' if current_alias else ' '} {alias}")
            line.append(f" → v{version}", style="dim")
            if current_alias:
                line.append("  (current)", style="dim")
            option_list.add_option(Option(line, id=f"alias:{alias}"))

        option_list.add_option(Option(Text(""), disabled=True))
        option_list.add_option(Option(Text("Versions", style="bold"), disabled=True))
        for version in sorted(self._versions, key=lambda v: v.version, reverse=True):
            line = Text(no_wrap=True)
            is_current = target.alias is None and version.version == target.current
            line.append(f"  {'›' if is_current else ' '} v{version.version}")
            if is_current:
                line.append("  (current)", style="dim")
            if version.version == newest:
                line.append("  (latest)", style="dim")
            option_list.add_option(Option(line, id=f"version:{version.version}"))

        option_list.highlighted = next(
            (i for i, opt in enumerate(option_list.options) if not opt.disabled), 0
        )
        self._update_preview()

    def _update_preview(self) -> None:
        body = self.query_one("#skillsbrowser-preview-body", NoMarkupStatic)
        if self._scope_target is not None:
            target = self._scope_target
            where = f" v{target.version}" if target.version is not None else ""
            body.update(f"{target.name}{where}\n\nChoose where to install this skill.")
            return
        if self._version_view is not None:
            self._preview_version(body)
            return
        option_id = self._highlighted_id()
        if option_id.startswith("installed:"):
            info = self._highlighted_installed()
            if info is None:
                return
            parts = [
                info.scope.capitalize(),
                info.source.capitalize(),
                _pin_label(info),
            ]
            meta = "  ·  ".join(part for part in parts if part)
            body.update(f"{info.name}\n{meta}\n\n{info.prompt or '(empty skill body)'}")
        elif option_id.startswith("catalog:"):
            skill_id = option_id.removeprefix("catalog:")
            entry = next((c for c in self._catalog if c.skill_id == skill_id), None)
            header = entry.name if entry is not None else skill_id
            if entry is not None and entry.description:
                header += f"\n{entry.description}"
            self._render_body(body, header, skill_id, None)
        else:
            body.update("")

    def _preview_version(self, body: NoMarkupStatic) -> None:
        target = self._version_view
        if target is None:
            return
        option_id = self._highlighted_id()
        if option_id.startswith("alias:"):
            alias = option_id.removeprefix("alias:")
            version = (
                self._latest_target()
                if alias == REGISTRY_LATEST_ALIAS
                else self._alias_targets.get(alias)
            )
            if version is None:
                body.update(f"{target.name}\n{alias}")
                return
            self._render_body(
                body, f"{target.name}  {alias} → v{version}", target.skill_id, version
            )
            return
        if not option_id.startswith("version:"):
            body.update("")
            return
        version = int(option_id.removeprefix("version:"))
        self._render_body(body, f"{target.name}  v{version}", target.skill_id, version)

    def _latest_target(self) -> int | None:
        """The concrete version the reserved ``latest`` alias resolves to."""
        return max((v.version for v in self._versions), default=None)

    def _render_body(
        self, body: NoMarkupStatic, header: str, skill_id: str, version: int | None
    ) -> None:
        cached = self._body_cache.get((skill_id, version))
        if cached is not None:
            body.update(f"{header}\n\n{cached or '(empty skill body)'}")
            return
        body.update(f"{header}\n\nLoading preview…")
        self.run_worker(
            self._fetch_body(skill_id, version), exclusive=True, group="preview"
        )

    async def _fetch_body(self, skill_id: str, version: int | None) -> None:
        response = await self._actions.detail(skill_id, version=version)
        text = response.detail.body if response.detail is not None else response.body
        self._body_cache[(skill_id, version)] = text or ""
        if self.is_mounted:
            self._update_preview()

    def _run(self, coro: Awaitable[None]) -> None:
        self.run_worker(coro, exclusive=True, group="skills")

    async def _mutate(self, status: str, coro: Awaitable[object]) -> bool:
        if self._busy:
            close = getattr(coro, "close", None)
            if callable(close):
                close()
            return False
        self._busy = True
        if status:
            self._set_status(status)
        try:
            await coro
            self._installed = list(await self._on_changed())
        except Exception as exc:
            logger.warning("Skill action failed: %s", exc)
            self._set_status(f"Action failed: {exc}")
            return False
        finally:
            self._busy = False
        return True

    def _begin_import(self, target: _ImportTarget) -> None:
        """Import ``target``; ask Global vs Project first when a project exists."""
        if not self._project_available:
            self._run(self._do_import("global", target))
            return
        self._scope_target = target
        self._show_scope_chooser(target.name)

    def _show_scope_chooser(self, name: str) -> None:
        option_list = self.query_one(OptionList)
        option_list.clear_options()
        self.query_one("#skillsbrowser-title", NoMarkupStatic).update(
            f"Import {name} into…"
        )
        self._set_search_visible(False)
        self._set_preview_visible(True)
        self._set_help(_STEP_HELP)
        globally = Text(no_wrap=True)
        globally.append("  Global")
        globally.append("   available in every project", style="dim")
        option_list.add_option(Option(globally, id="scope:global"))
        project = Text(no_wrap=True)
        project.append("  Project")
        project.append("  committed to this project's .vibe/skills.toml", style="dim")
        option_list.add_option(Option(project, id="scope:project"))
        option_list.highlighted = 0
        self._update_preview()

    async def _do_import(self, scope: str, target: _ImportTarget | None = None) -> None:
        target = target or self._scope_target
        if target is None:
            return
        action = self._actions.import_skill(
            target.skill_id, version=target.version, alias=target.alias, scope=scope
        )
        if await self._mutate("Importing…", action):
            self._scope_target = None
            self._version_view = None
            self._versions = []
            self._show_list()

    async def _remove(self, name: str, scope: str) -> None:
        if await self._mutate("Removing…", self._actions.remove(name, scope)):
            self._show_list()

    async def _toggle_enabled(self, name: str, enabled: bool) -> None:
        if await self._mutate("", self._actions.set_enabled(name, enabled)):
            self._show_list()

    async def _pin_version(self, version: int) -> None:
        target = self._version_view
        if target is None:
            return
        if not target.installed:
            self._begin_import(
                _ImportTarget(target.skill_id, target.name, version=version)
            )
            return
        action = self._actions.set_version(target.name, version, target.scope)
        if await self._mutate(f"Pinning v{version}…", action):
            self._after_pin(target.name, target.scope)

    async def _pin_alias(self, alias: str) -> None:
        target = self._version_view
        if target is None:
            return
        if not target.installed:
            new_alias = None if alias == REGISTRY_LATEST_ALIAS else alias
            self._begin_import(
                _ImportTarget(target.skill_id, target.name, alias=new_alias)
            )
            return
        if alias == REGISTRY_LATEST_ALIAS:
            action = self._actions.set_latest(target.name, target.scope)
        else:
            action = self._actions.set_alias(target.name, alias, target.scope)
        if await self._mutate(f"Tracking {alias}…", action):
            self._after_pin(target.name, target.scope)

    def _after_pin(self, name: str, scope: str) -> None:
        info = next(
            (
                i
                for i in self._installed
                if i.name == name
                and i.scope == scope
                and i.source == "registry"
                and i.registry is not None
            ),
            None,
        )
        if info is not None and info.registry is not None:
            self._version_view = _VersionTarget(
                name=info.name,
                skill_id=info.registry.skill_id,
                scope=info.scope,
                current=info.registry.version,
                installed=True,
                alias=info.registry.alias,
            )
            self._show_versions()
        else:
            self._version_view = None
            self._show_list()

    def _set_help(self, text: str) -> None:
        self.query_one("#skillsbrowser-help", NoMarkupStatic).update(
            shortcut_hint(text)
        )

    def _set_status(self, text: str) -> None:
        self.query_one("#skillsbrowser-help", NoMarkupStatic).update(text)
