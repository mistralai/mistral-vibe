from __future__ import annotations

from typing import ClassVar

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList
from textual.widgets.option_list import Option, OptionDoesNotExist

from vibe.app_server.protocol import (
    ConfigFieldKind,
    ConfigFieldWire,
    ConfigWriteOpWire,
    ConfigWriteResponse,
)
from vibe.app_server.resources import ConfigResource
from vibe.cli.textual_ui.constants import UNPINNED_ACTIVE_MODEL
from vibe.cli.textual_ui.screens.config._common import (
    ADVANCED_HEADER,
    CONFIG_SCREEN_ID,
    DEFAULT_ORIGIN,
    MERGE_THRESHOLD,
    POPULAR_HEADER,
    ConfigOptionList,
    enforced_legend,
    filter_field_views,
    is_enforced,
    origin_label,
    row_text,
    search_text,
    section_header_text,
)
from vibe.cli.textual_ui.screens.config.edit import prompt_field_value
from vibe.cli.textual_ui.shortcut_hints import shortcut, shortcut_hint
from vibe.cli.textual_ui.widgets.no_markup_static import NoMarkupStatic
from vibe.cli.textual_ui.widgets.theme_picker import sorted_theme_names


class ConfigScreen(ModalScreen[bool]):
    """Full-screen, searchable settings browser with per-layer origin display."""

    SCOPED_CSS = False
    CSS_PATH = "config_screen.tcss"

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "close", "Close", show=False),
        # Non-printable so they never collide with type-to-filter.
        Binding("ctrl+r", "reset", "Reset to default", show=False),
    ]

    def __init__(self, config: ConfigResource) -> None:
        super().__init__(id=CONFIG_SCREEN_ID)
        self._config = config
        self._views: list[ConfigFieldWire] = []
        self._filtered: list[ConfigFieldWire] = []
        self._rendered_ids: list[str | None] = []
        self._targets: tuple[str, ...] = ()
        self._query = ""
        self._dirty = False
        self._cursor_name: str | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="config-screen-content"):
            yield NoMarkupStatic(
                search_text(self._query),
                id="config-screen-search",
                classes="config-screen-search",
            )
            yield NoMarkupStatic(
                "", id="config-screen-legend", classes="config-screen-legend"
            )
            with Horizontal(id="config-screen-options-wrap"):
                yield ConfigOptionList(
                    on_query_changed=self._filter, id="config-screen-options"
                )
            yield NoMarkupStatic(
                shortcut_hint(
                    f"{shortcut('type')} Filter  {shortcut('↑↓')} Navigate  "
                    f"{shortcut('Enter')} Edit  {shortcut('Ctrl+R')} Reset  "
                    f"{shortcut('Esc')} Close"
                ),
                classes="config-screen-help",
            )

    async def on_mount(self) -> None:
        content = self.query_one("#config-screen-content")
        content.border_title = "Settings"
        await self._sync_views()
        self._refresh_options()
        self.query_one(OptionList).focus()

    def _filter(self, query: str) -> None:
        self._query = query
        self.query_one("#config-screen-search", NoMarkupStatic).update(
            search_text(query)
        )
        self._refresh_options(preserve_highlight=False)

    def _apply_dynamic_choices(self, view: ConfigFieldWire) -> ConfigFieldWire:
        # These fields' choices depend on client/runtime state (registered
        # themes, configured models), so the server sends them as plain strings;
        # promote them to enums here.
        config = self._config.current
        choices: dict[str, list[str]] = {
            "theme": list(sorted_theme_names()),
            "active_model": [model.alias for model in config.models],
            "active_transcribe_model": list(config.transcribe_models),
            "active_tts_model": list(config.tts_models),
        }
        if (field_choices := choices.get(view.name)) is None:
            return view
        update: dict[str, object] = {
            "kind": ConfigFieldKind.ENUM,
            "enum_choices": field_choices,
        }
        if view.name == "active_model":
            update["enum_choices"] = [UNPINNED_ACTIVE_MODEL, *field_choices]
            update["value_labels"] = {
                UNPINNED_ACTIVE_MODEL: f"default (currently {config.default_model_alias})"
            }
        return view.model_copy(update=update)

    def _sections(self) -> list[tuple[str | None, list[ConfigFieldWire]]]:
        popular = [v for v in self._views if v.popular]
        advanced = [v for v in self._views if not v.popular]
        if not self._query.strip():
            return [(POPULAR_HEADER, popular), (ADVANCED_HEADER, advanced)]

        popular_hits = filter_field_views(popular, self._query)
        advanced_hits = filter_field_views(advanced, self._query)
        if len(popular_hits) + len(advanced_hits) <= MERGE_THRESHOLD:
            merged = filter_field_views(
                self._views,
                self._query,
                boost_names=frozenset(v.name for v in self._views if v.popular),
            )
            return [(None, merged)]

        sections: list[tuple[str | None, list[ConfigFieldWire]]] = []
        if popular_hits:
            sections.append((POPULAR_HEADER, popular_hits))
        if advanced_hits:
            sections.append((ADVANCED_HEADER, advanced_hits))
        return sections

    def _refresh_options(self, *, preserve_highlight: bool = True) -> None:
        previous = self._highlighted_name() if preserve_highlight else None
        sections = self._sections()
        self._filtered = [view for _, views in sections for view in views]
        self._refresh_legend()
        options: list[Option] = []
        self._rendered_ids = []
        first_header = True
        for label, views in sections:
            if label is not None:
                options.append(
                    Option(
                        section_header_text(label, first=first_header), disabled=True
                    )
                )
                self._rendered_ids.append(None)
                first_header = False
            for view in views:
                options.append(Option(row_text(view), id=view.name))
                self._rendered_ids.append(view.name)
        option_list = self.query_one(OptionList)
        option_list.clear_options()
        option_list.add_options(options)
        if not self._rendered_ids:
            return
        option_list.highlighted = (
            self._rendered_ids.index(previous)
            if previous is not None and previous in self._rendered_ids
            else next(
                (i for i, name in enumerate(self._rendered_ids) if name is not None), 0
            )
        )

    def _highlighted_name(self) -> str | None:
        option = self.query_one(OptionList).highlighted_option
        if option is None or option.id is None:
            return None
        return str(option.id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # Fires on Enter or a double click; a single click only highlights.
        if event.option.id is not None:
            if (view := self._view_by_name(str(event.option.id))) is not None:
                self._edit(view)

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        option = event.option
        name = str(option.id) if option is not None and option.id is not None else None
        self._set_cursor(name)

    def _set_cursor(self, name: str | None) -> None:
        if name == self._cursor_name:
            return
        previous = self._cursor_name
        self._cursor_name = name
        for cursor in (previous, name):
            if cursor is None or (view := self._view_by_name(cursor)) is None:
                continue
            try:
                self.query_one(OptionList).replace_option_prompt(
                    cursor, row_text(view, selected=cursor == self._cursor_name)
                )
            except OptionDoesNotExist:
                pass

    def _view_by_name(self, name: str) -> ConfigFieldWire | None:
        return next((v for v in self._views if v.name == name), None)

    def _refresh_legend(self) -> None:
        enforced = any(is_enforced(view) for view in self._filtered)
        legend = self.query_one("#config-screen-legend", NoMarkupStatic)
        legend.display = enforced
        legend.update(enforced_legend() if enforced else "")

    @work
    async def _edit(self, view: ConfigFieldWire) -> None:
        # Enforced fields are read-only; the persistent legend already explains
        # this, so no notification is raised here.
        if is_enforced(view):
            return
        result = await prompt_field_value(self.app, view, self._targets)
        if result is not None:
            value, target = result
            await self._write(
                view,
                [
                    ConfigWriteOpWire(
                        op="set", path=view.path, value=value, target_layer=target
                    )
                ],
                reason="config screen edit",
            )

    def action_reset(self) -> None:
        if (name := self._highlighted_name()) is not None:
            if (view := self._view_by_name(name)) is not None:
                self._reset(view)

    @work
    async def _reset(self, view: ConfigFieldWire) -> None:
        if is_enforced(view):
            return
        layer_values = [
            entry for entry in view.layer_values if entry.layer != DEFAULT_ORIGIN
        ]
        if not layer_values:
            self.notify(
                f"'{view.name}' has nothing to clear.",
                severity="information",
                markup=False,
            )
            return
        top = layer_values[0].layer
        if top not in set(self._targets):
            # A read-only layer (e.g. environment) is shadowing the writable
            # ones; clearing underneath it would silently mutate a hidden value.
            self.notify(
                f"'{view.name}' is pinned by {origin_label(top)}; nothing to clear.",
                severity="information",
                markup=False,
            )
            return
        await self._write(
            view,
            [ConfigWriteOpWire(op="remove", path=view.path, target_layer=top)],
            reason="config screen reset",
        )

    async def _write(
        self, view: ConfigFieldWire, ops: list[ConfigWriteOpWire], *, reason: str
    ) -> None:
        response: ConfigWriteResponse = await self._config.write(ops, reason=reason)
        if response.rejected:
            self.notify(
                f"'{view.name}' rejected this value.", severity="error", markup=False
            )
            return
        if response.failures:
            self.notify(
                f"Could not save '{view.name}'.", severity="error", markup=False
            )
            return
        self._dirty = True
        await self._sync_views()
        self._refresh_options()

    async def _sync_views(self) -> None:
        response = await self._config.read_fields()
        self._targets = tuple(response.targets)
        self._views = [self._apply_dynamic_choices(wire) for wire in response.fields]

    def action_close(self) -> None:
        self.dismiss(self._dirty)
