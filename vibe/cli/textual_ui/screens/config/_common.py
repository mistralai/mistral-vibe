from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from textual import events
from textual.content import Content
from textual.css.scalar import Scalar
from textual.screen import Screen
from textual.widget import Widget

from vibe.app_server.protocol import ConfigFieldWire, ConfigLayerValueWire
from vibe.cli.autocompletion.fuzzy import fuzzy_match
from vibe.cli.textual_ui.widgets.navigable_option_list import NavigableOptionList

CONFIG_SCREEN_ID = "config-screen"
CONFIG_EDIT_SCREEN_ID = "config-edit-screen"

CONFIG_MODAL_IDS = frozenset({CONFIG_SCREEN_ID, CONFIG_EDIT_SCREEN_ID})

# Each config modal stacked on top of another renders strictly narrower — both
# the percentage and the absolute cap shrink per level — so the stack depth
# reads at a glance on any terminal size.
_STACK_BASE_WIDTH_PCT = 96
_STACK_WIDTH_STEP_PCT = 5
_STACK_MIN_WIDTH_PCT = 60
_STACK_BASE_MAX_WIDTH = 86
_STACK_MAX_WIDTH_STEP = 6


def _config_stack_depth(screen: Screen[Any]) -> int:
    stack = screen.app.screen_stack
    if screen not in stack:
        return 0
    index = stack.index(screen)
    return sum(1 for below in stack[:index] if below.id in CONFIG_MODAL_IDS)


def apply_stacked_width(screen: Screen[Any], content: Widget) -> None:
    depth = _config_stack_depth(screen)
    if depth <= 0:
        return
    pct = max(
        _STACK_MIN_WIDTH_PCT, _STACK_BASE_WIDTH_PCT - depth * _STACK_WIDTH_STEP_PCT
    )
    content.styles.width = Scalar.parse(f"{pct}%")
    content.styles.max_width = _STACK_BASE_MAX_WIDTH - depth * _STACK_MAX_WIDTH_STEP


DEFAULT_ORIGIN = "default"
OVERRIDES_LAYER = "overrides"
ENVIRONMENT_LAYER = "environment"

NAME_COLUMN_WIDTH = 38
VALUE_COLUMN_WIDTH = 32
COLUMN_GAP = 2
DOUBLE_CLICK = 2

CURSOR = "▸ "
CURSOR_BLANK = "  "
CURSOR_WIDTH = len(CURSOR)

ROW_WIDTH = CURSOR_WIDTH + NAME_COLUMN_WIDTH + COLUMN_GAP + VALUE_COLUMN_WIDTH
# Text width inside the edit-modal side panel (#config-edit-side): its fixed
# width minus the left border and horizontal padding. Keep in sync with
# edit.tcss (#config-edit-side).
INSPECTOR_INNER_WIDTH = 33
POPULAR_HEADER = "Popular settings"
ADVANCED_HEADER = "Advanced settings"
# When a filter narrows results to at most this many, drop the section split and
# show one relevance-ranked list instead.
MERGE_THRESHOLD = 5
# Multiplicative bump applied to boosted (popular) fields so they win near-ties
# without ever burying a much stronger match from an unboosted field.
_BOOST_FACTOR = 1.25


def format_value(value: Any) -> str:
    match value:
        case bool():
            return "True" if value else "False"
        case str():
            return value if value else '""'
        case list():
            return f"[{len(value)} item{'s' if len(value) != 1 else ''}]"
        case dict():
            return f"{{{len(value)} entr{'ies' if len(value) != 1 else 'y'}}}"
        case None:
            return "—"
        case _:
            return str(value)


def columns_header() -> Content:
    header = (
        f"{' ' * CURSOR_WIDTH}{'SETTING':<{NAME_COLUMN_WIDTH}}{' ' * COLUMN_GAP}VALUE"
    )
    return Content.styled(header, "dim")


def search_text(query: str, placeholder: str = "type to filter") -> Content:
    return Content.assemble(
        ("Filter: ", "bold"), query if query else (placeholder, "dim")
    )


def origin_style(origin: str) -> str:
    if origin == DEFAULT_ORIGIN:
        return "$text-muted"
    return "$foreground"


def origin_label(origin: str) -> str:
    if origin == DEFAULT_ORIGIN:
        return "defaults"
    if origin == OVERRIDES_LAYER:
        return "temporary"
    if origin == ENVIRONMENT_LAYER:
        return "env"
    if origin.endswith("-toml"):
        return f"{origin[: -len('-toml')]} config"
    return origin


def truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 1:
        return value[:width]
    return f"{value[: width - 1]}…"


def section_header_text(label: str, *, first: bool) -> Content:
    pad = max(0, ROW_WIDTH - len(label) - 2)
    left = pad // 2
    return Content.assemble(
        "" if first else "\n\n",
        ("─" * left, "dim"),
        (f" {label} ", "bold"),
        ("─" * (pad - left), "dim"),
        "\n\n",
        columns_header(),
    )


def row_text(view: ConfigFieldWire, *, selected: bool = False) -> Content:
    name = truncate(view.name, NAME_COLUMN_WIDTH)
    label = truncate(format_value(view.value), VALUE_COLUMN_WIDTH)
    return Content.assemble(
        (CURSOR if selected else CURSOR_BLANK, "$primary bold"),
        (name, "bold"),
        " " * (NAME_COLUMN_WIDTH - len(name) + COLUMN_GAP),
        (label, "$text-muted"),
    )


def inspector_text(layer_values: list[ConfigLayerValueWire]) -> Content:
    if not layer_values:
        return Content.styled("default only", "dim")
    width = max(len(origin_label(entry.layer)) for entry in layer_values)
    # Fit the value on one line: the side panel is fixed-width, so cap the value
    # to whatever space is left after the cursor, label column, and gap.
    value_budget = max(1, INSPECTOR_INNER_WIDTH - CURSOR_WIDTH - width - COLUMN_GAP)
    last = len(layer_values) - 1
    parts: list[str | tuple[str, str]] = []
    for index, entry in enumerate(layer_values):
        active = index == 0
        style = origin_style(entry.layer) if active else "dim"
        parts.append(("▸ " if active else "  ", style))
        parts.append((f"{origin_label(entry.layer):<{width}}", style))
        parts.append(" " * COLUMN_GAP)
        value = truncate(format_value(entry.value), value_budget)
        parts.append((value, "bold" if active else "dim"))
        if index != last:
            parts.append("\n")
    return Content.assemble(*parts)


def target_hint(name: str) -> str:
    return "until restart" if name == OVERRIDES_LAYER else "saved"


def target_bar_text(target: str, targets: tuple[str, ...]) -> Content:
    parts: list[str | tuple[str, str]] = [("Save to", "bold"), "   "]
    for index, name in enumerate(targets):
        if index:
            parts.append("    ")
        active = name == target
        style = origin_style(name)
        parts.append(("● " if active else "○ ", style if active else "dim"))
        parts.append((origin_label(name), f"{style} bold" if active else "dim"))
        parts.append((f" ({target_hint(name)})", "dim"))
    if len(targets) > 1:
        parts.append("     ")
        parts.append(("Tab to switch", "dim"))
    return Content.assemble(*parts)


class ConfigOptionList(NavigableOptionList):
    """Results list that stays focused and routes printable keys to a filter.

    Arrow/Enter navigation is inherited from OptionList; printable keys and
    Backspace are forwarded to the parent screen instead of triggering any
    list behaviour.
    """

    def __init__(
        self, *, on_query_changed: Callable[[str], None], **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._on_query_changed = on_query_changed
        self._query = ""

    def on_key(self, event: events.Key) -> None:
        if event.key == "backspace":
            self._query = self._query[:-1]
        elif (char := event.character) is not None and len(char) == 1:
            if not char.isprintable():
                return
            self._query += char
        else:
            return
        self._on_query_changed(self._query)
        event.stop()
        event.prevent_default()

    async def _on_click(self, event: events.Click) -> None:
        # Textual dispatches _on_click for every class in the MRO, so the base
        # OptionList._on_click (which selects) still runs after this one unless
        # we prevent the default. A double click falls through to select/edit;
        # a single click only moves the highlight.
        if event.chain >= DOUBLE_CLICK:
            return
        event.prevent_default()
        clicked = event.style.meta.get("option")
        if clicked is None:
            return
        if not self.get_option_at_index(clicked).disabled:
            self.highlighted = clicked


type EditResult = tuple[Any, str]


def filter_field_views(
    views: Sequence[ConfigFieldWire],
    query: str,
    boost_names: frozenset[str] = frozenset(),
) -> list[ConfigFieldWire]:
    needle = query.strip()
    if not needle:
        return list(views)

    scored: list[tuple[float, int, ConfigFieldWire]] = []
    for index, view in enumerate(views):
        name_match = fuzzy_match(needle, view.name)
        desc_match = fuzzy_match(needle, view.description)
        # Description matches are weaker signals than name matches.
        score = max(name_match.score, desc_match.score * 0.5)
        if view.name in boost_names:
            score *= _BOOST_FACTOR
        if name_match.matched or desc_match.matched:
            scored.append((score, index, view))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [view for _, _, view in scored]
