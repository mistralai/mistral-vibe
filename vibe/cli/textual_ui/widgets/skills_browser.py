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
from typing import ClassVar, Protocol

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.events import DescendantBlur, Key, Resize
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

_DESC_BREAKPOINT = 84

_TABS: tuple[tuple[str, str], ...] = (
    ("tab-installed", "Installed"),
    ("tab-available", "Available"),
    ("tab-updates", "Updates"),
)

_TAB_HINT = f"{shortcut('←→')} Tabs"
_LIST_HELP_INSTALLED = (
    f"{shortcut('↑↓/jk')} Navigate  {_TAB_HINT}  {shortcut('/')} Search  "
    f"{shortcut('v')} Versions  {shortcut('x')} Remove  {shortcut('Esc')} Close"
)
_LIST_HELP_CATALOG = (
    f"{shortcut('↑↓/jk')} Navigate  {_TAB_HINT}  {shortcut('Enter')} Import  "
    f"{shortcut('/')} Search  {shortcut('v')} Versions  {shortcut('Esc')} Close"
)
_LIST_HELP_UPDATES = (
    f"{shortcut('↑↓/jk')} Navigate  {_TAB_HINT}  {shortcut('/')} Search  "
    f"{shortcut('v')} Versions  {shortcut('x')} Remove  {shortcut('Esc')} Close"
)
_VERSION_HELP = (
    f"{shortcut('↑↓/jk')} Navigate  {shortcut('Enter')} Pin  "
    f"{shortcut('Backspace')} Back  {shortcut('Esc')} Close"
)


@dataclass
class _VersionTarget:
    """The skill whose versions are being browsed (installed pin or catalog)."""

    name: str
    skill_id: str
    scope: str
    current: int | None
    installed: bool
    alias: str | None = None  # the alias the pin currently follows, if any


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

    async def read_installed(self) -> list[SkillSummary]: ...


def _short(text: str, limit: int = 48) -> str:
    """A single-line, length-capped description for inline row display."""
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _pin_label(info: SkillSummary) -> str:
    if info.source != "registry" or info.registry is None:
        return "local"
    reg = info.registry
    if reg.alias == REGISTRY_LATEST_ALIAS:
        return f"latest (v{reg.version})"
    if reg.alias:
        return f"{reg.alias} (v{reg.version})"
    return f"v{reg.version}"


class SkillsBrowserApp(Container):
    """Browse, import, pin, and remove skills."""

    can_focus_children = True
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=False),
        Binding("backspace", "back", "Back", show=False),
        Binding("v", "versions", "Versions", show=False),
        Binding("x", "remove", "Remove", show=False),
        Binding("p", "pin_project", "Project", show=False),
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
        self._alias_targets: dict[str, int] = {}
        self._body_cache: dict[tuple[str, int | None], str] = {}
        self._busy = False
        self._query = ""
        self._tab = _TABS[0][0]
        self._showing_descriptions = False

    def compose(self) -> ComposeResult:
        with Vertical(id="skillsbrowser-content"):
            yield NoMarkupStatic("", id="skillsbrowser-title", classes="settings-title")
            yield NoMarkupStatic("", id="skillsbrowser-tabs")
            yield VscodeCompatInput(
                placeholder="Search skills…", id="skillsbrowser-search"
            )
            with Horizontal(id="skillsbrowser-body"):
                yield NavigableOptionList(id="skillsbrowser-options")
                with VerticalScroll(id="skillsbrowser-preview"):
                    yield NoMarkupStatic("", id="skillsbrowser-preview-body")
            yield NoMarkupStatic("", id="skillsbrowser-help", classes="settings-help")

    def on_mount(self) -> None:
        if not self._installed:
            self._tab = "tab-available"
        self._render_tabs()
        self._show_list()
        self.query_one(OptionList).focus()

    def on_resize(self, event: Resize) -> None:
        wide = event.size.width >= _DESC_BREAKPOINT
        if wide == self._showing_descriptions:
            return
        self._showing_descriptions = wide
        if self._version_view is None:
            self._show_list()
        if not self._searching:
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
        if self._version_view is not None or self._searching:
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
        if self._version_view is not None:
            return
        self.query_one("#skillsbrowser-search", Input).focus()

    def on_key(self, event: Key) -> None:
        if event.key == "escape" and self._searching:
            event.stop()
            event.prevent_default()
            self._exit_search(clear=True)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "skillsbrowser-search":
            return
        self._query = event.value
        if self._version_view is None:
            self._show_list()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "skillsbrowser-search":
            self._exit_search(clear=False)

    def _exit_search(self, *, clear: bool) -> None:
        if clear and self._query:
            self._query = ""
            self.query_one("#skillsbrowser-search", Input).value = ""
            if self._version_view is None:
                self._show_list()
        self.query_one(OptionList).focus()

    def _matches(self, *fields: str | None) -> bool:
        if not self._query:
            return True
        needle = self._query.casefold()
        return any(field and needle in field.casefold() for field in fields)

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
        option_id = event.option.id or ""
        if option_id.startswith("catalog:"):
            self._run(self._import(option_id.removeprefix("catalog:")))
        elif option_id.startswith("alias:"):
            self._run(self._pin_alias(option_id.removeprefix("alias:")))
        elif option_id.startswith("version:"):
            self._run(self._pin_version(int(option_id.removeprefix("version:"))))

    def action_back(self) -> None:
        if self._version_view is not None:
            self._version_view = None
            self._versions = []
            self._show_list()

    def action_close(self) -> None:
        self.post_message(self.Closed())

    def action_versions(self) -> None:
        if self._version_view is not None:
            return
        target = self._version_target()
        if target is not None:
            self._run(self._open_versions(target))

    def action_remove(self) -> None:
        if self._version_view is not None:
            return
        info = self._highlighted_installed()
        if info is not None and info.source == "registry":
            self._run(self._remove(info.name, info.scope))

    def action_pin_project(self) -> None:
        """Import/pin the highlighted catalog skill into the project scope.

        Enter always targets the global scope; ``p`` mirrors it into the
        project manifest when one is available. Only unimported catalog rows
        and their versions are project-targetable (installed pins already own a
        scope).
        """
        if not self._project_available:
            return
        if self._version_view is not None:
            if self._version_view.installed:
                return
            option_id = self._highlighted_id()
            if option_id.startswith("alias:"):
                self._run(
                    self._pin_alias(option_id.removeprefix("alias:"), scope="project")
                )
            elif option_id.startswith("version:"):
                self._run(
                    self._pin_version(
                        int(option_id.removeprefix("version:")), scope="project"
                    )
                )
            return
        option_id = self._highlighted_id()
        if option_id.startswith("catalog:"):
            self._run(self._import(option_id.removeprefix("catalog:"), scope="project"))

    def _version_target(self) -> _VersionTarget | None:
        """The registry skill under the cursor (installed pin or catalog entry)."""
        option_id = self._highlighted_id()
        if option_id.startswith("installed:"):
            info = self._installed[int(option_id.removeprefix("installed:"))]
            if info.source != "registry" or info.registry is None:
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
        option_list.clear_options()
        self.query_one("#skillsbrowser-title", NoMarkupStatic).update("Skills")

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
            (index, info)
            for index, info in enumerate(self._installed)
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
        self._showing_descriptions = self.size.width >= _DESC_BREAKPOINT
        show_desc = self._showing_descriptions

        if self._tab == "tab-available":
            self._set_help(self._with_project(_LIST_HELP_CATALOG))
            ordered = sorted(importable, key=lambda c: c.name.casefold())
            for entry in ordered:
                option_list.add_option(
                    Option(
                        self._catalog_row(entry, width, show_desc),
                        id=f"catalog:{entry.skill_id}",
                    )
                )
            self._finish_list(option_list, bool(ordered), self._empty_message())
            return

        if self._tab == "tab-updates":
            self._set_help(_LIST_HELP_UPDATES)
            updates = [(i, info) for i, info in installed if self._has_update(info)]
            for index, info in updates:
                option_list.add_option(
                    Option(
                        self._installed_row(info, width, show_desc),
                        id=f"installed:{index}",
                    )
                )
            self._finish_list(
                option_list,
                bool(updates),
                "No matching updates" if self._query else "No updates available",
            )
            return

        self._set_help(_LIST_HELP_INSTALLED)
        self._build_installed_grouped(option_list, installed, width, show_desc)
        self._finish_list(
            option_list,
            bool(installed),
            "No matching skills" if self._query else "No skills installed",
        )

    def _build_installed_grouped(
        self,
        option_list: OptionList,
        installed: list[tuple[int, SkillSummary]],
        width: int,
        show_desc: bool,
    ) -> None:
        groups: list[tuple[str, list[tuple[int, SkillSummary]]]] = [
            ("Global", [r for r in installed if r[1].scope == "global"]),
            ("Project", [r for r in installed if r[1].scope == "project"]),
            ("", [r for r in installed if r[1].scope not in {"global", "project"}]),
        ]
        first = True
        for label, rows in groups:
            if not rows:
                continue
            if not first:
                option_list.add_option(Option(Text(""), disabled=True))
            if label:
                option_list.add_option(Option(Text(label, style="bold"), disabled=True))
            for index, info in rows:
                option_list.add_option(
                    Option(
                        self._installed_row(info, width, show_desc),
                        id=f"installed:{index}",
                    )
                )
            first = False

    def _finish_list(
        self, option_list: OptionList, has_rows: bool, empty: str | Text
    ) -> None:
        if not has_rows:
            option_list.add_option(Option(empty, disabled=True))
            self.query_one("#skillsbrowser-preview-body", NoMarkupStatic).update("")
            return
        option_list.highlighted = next(
            (i for i, opt in enumerate(option_list.options) if not opt.disabled), 0
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

    def _installed_row(
        self, info: SkillSummary, width: int, show_desc: bool = False
    ) -> Text:
        badges = [info.source]
        if info.source in {"local", "registry"}:
            badges.append(info.scope)
        if info.source == "registry" and info.registry is not None:
            reg = info.registry
            badges.append(
                f"{reg.alias} v{reg.version}" if reg.alias else f"v{reg.version}"
            )
        row = Text(no_wrap=True)
        row.append(f"  {info.name:<{width}}")
        row.append(f"  [{' · '.join(badges)}]", style="dim")
        if self._has_update(info):
            row.append("  ")
            row.append("●", style="blue")
            row.append(f" new v{self._updates[info.name]}", style="blue")
        elif show_desc and info.description:
            row.append(f"  {_short(info.description)}", style="dim italic")
        return row

    def _catalog_row(
        self, entry: SkillCatalogEntry, width: int, show_desc: bool = False
    ) -> Text:
        badges = ["shared"]
        if entry.sharing_scope in {"private", "workspace"}:
            badges.append(entry.sharing_scope)
        badges.append(f"v{entry.latest_version}")
        row = Text(no_wrap=True)
        row.append(f"  {entry.name:<{width}}")
        row.append(f"  [{' · '.join(badges)}]", style="dim")
        if show_desc and entry.description:
            row.append(f"  {_short(entry.description)}", style="dim italic")
        return row

    def _highlighted_installed(self) -> SkillSummary | None:
        option_id = self._highlighted_id()
        if not option_id.startswith("installed:"):
            return None
        return self._installed[int(option_id.removeprefix("installed:"))]

    def _highlighted_id(self) -> str:
        option_list = self.query_one(OptionList)
        highlighted = option_list.highlighted
        if highlighted is None:
            return ""
        return option_list.get_option_at_index(highlighted).id or ""

    async def _open_versions(self, target: _VersionTarget) -> None:
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
        self.query_one("#skillsbrowser-title", NoMarkupStatic).update(
            f"{target.name}: versions"
        )
        self._set_help(
            self._with_project(_VERSION_HELP) if not target.installed else _VERSION_HELP
        )
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
        if self._version_view is not None:
            self._preview_version(body)
            return
        option_id = self._highlighted_id()
        if option_id.startswith("installed:"):
            info = self._installed[int(option_id.removeprefix("installed:"))]
            header = (
                f"{info.name}\n{_pin_label(info)}  ·  {info.scope}  ·  {info.source}"
            )
            body.update(f"{header}\n\n{info.prompt or '(empty skill body)'}")
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
            return False
        self._busy = True
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

    async def _import(self, skill_id: str, *, scope: str = "global") -> None:
        action = self._actions.import_skill(skill_id, scope=scope)
        if await self._mutate("Importing…", action):
            self._show_list()

    async def _remove(self, name: str, scope: str) -> None:
        if await self._mutate("Removing…", self._actions.remove(name, scope)):
            self._show_list()

    async def _pin_version(self, version: int, *, scope: str = "global") -> None:
        target = self._version_view
        if target is None:
            return
        if target.installed:
            action = self._actions.set_version(target.name, version, target.scope)
        else:
            action = self._actions.import_skill(
                target.skill_id, version=version, scope=scope
            )
        effective_scope = target.scope if target.installed else scope
        if await self._mutate(f"Pinning v{version}…", action):
            self._after_pin(target.name, effective_scope)

    async def _pin_alias(self, alias: str, *, scope: str = "global") -> None:
        target = self._version_view
        if target is None:
            return
        if alias == REGISTRY_LATEST_ALIAS:
            action = (
                self._actions.set_latest(target.name, target.scope)
                if target.installed
                else self._actions.import_skill(target.skill_id, scope=scope)
            )
        else:
            action = (
                self._actions.set_alias(target.name, alias, target.scope)
                if target.installed
                else self._actions.import_skill(
                    target.skill_id, alias=alias, scope=scope
                )
            )
        effective_scope = target.scope if target.installed else scope
        if await self._mutate(f"Tracking {alias}…", action):
            self._after_pin(target.name, effective_scope)

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

    def _with_project(self, text: str) -> str:
        """Append the project-scope hint when a project manifest is available."""
        if self._project_available:
            return f"{text}  {shortcut('p')} Project"
        return text

    def _set_help(self, text: str) -> None:
        self.query_one("#skillsbrowser-help", NoMarkupStatic).update(
            shortcut_hint(text)
        )

    def _set_status(self, text: str) -> None:
        self.query_one("#skillsbrowser-title", NoMarkupStatic).update(text)
