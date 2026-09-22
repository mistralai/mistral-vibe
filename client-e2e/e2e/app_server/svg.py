"""Render a captured snapshot grid to SVG via Rich's terminal exporter."""

from __future__ import annotations

import copy
import io

from rich.color_triplet import ColorTriplet
from rich.console import Console
from rich.style import Style
from rich.terminal_theme import DEFAULT_TERMINAL_THEME
from rich.text import Text

from e2e.app_server.config import COLUMNS, ROWS
from e2e.pty.screen import Attr, Cell, Snapshot

_NAME_MAP = {
    "brown": "yellow",
    "brightblack": "bright_black",
    "brightred": "bright_red",
    "brightgreen": "bright_green",
    "brightbrown": "bright_yellow",
    "brightblue": "bright_blue",
    "brightmagenta": "bright_magenta",
    "brightcyan": "bright_cyan",
    "brightwhite": "bright_white",
}

_HEX = set("0123456789abcdefABCDEF")
_HEX_LEN = 6


def _rich_color(token: str) -> str | None:
    """Map a pyte color token to a Rich color, or None for the terminal default."""
    if token == "default":
        return None
    if len(token) == _HEX_LEN and all(ch in _HEX for ch in token):
        return f"#{token}"
    return _NAME_MAP.get(token, token)


def _row_text(cells: tuple[Cell, ...]) -> Text:
    """Build a Rich Text for one grid row, carrying each cell's fg/bg/attrs."""
    text = Text()
    for cell in cells:
        style = Style(
            color=_rich_color(cell.foreground),
            bgcolor=_rich_color(cell.background),
            bold=Attr.BOLD in cell.attrs,
            italic=Attr.ITALICS in cell.attrs,
            underline=Attr.UNDERSCORE in cell.attrs,
            strike=Attr.STRIKETHROUGH in cell.attrs,
            blink=Attr.BLINK in cell.attrs,
            reverse=Attr.REVERSE in cell.attrs,
            dim=Attr.DIM in cell.attrs,
        )
        text.append(cell.character or " ", style=style)
    return text


def _dominant_hex_bg(cells: tuple[tuple[Cell, ...], ...]) -> str | None:
    """The single hex bg covering most cells, if any (e.g. ratatui's fill)."""
    counts: dict[str, int] = {}
    for row in cells:
        for cell in row:
            bg = cell.background
            if len(bg) == _HEX_LEN and all(ch in _HEX for ch in bg):
                counts[bg] = counts.get(bg, 0) + 1
    if not counts:
        return None
    top = max(counts, key=lambda k: counts[k])
    return top if counts[top] > (ROWS * COLUMNS) // 2 else None


def to_svg(snapshot: Snapshot, title: str) -> str:
    """Render a snapshot's cell grid to an SVG string via Rich's exporter."""
    cells = snapshot.cells
    theme = None
    if (dom := _dominant_hex_bg(cells)) is not None:
        cells = tuple(
            tuple(
                Cell(
                    c.character,
                    c.foreground,
                    "default" if c.background == dom else c.background,
                    c.attrs,
                    c.hyperlink,
                )
                for c in row
            )
            for row in cells
        )
        theme = copy.copy(DEFAULT_TERMINAL_THEME)
        theme.background_color = ColorTriplet(
            int(dom[0:2], 16), int(dom[2:4], 16), int(dom[4:6], 16)
        )

    console = Console(
        width=COLUMNS,
        height=ROWS,
        record=True,
        file=io.StringIO(),
        color_system="truecolor",
    )
    for row in cells:
        console.print(_row_text(row), no_wrap=True, crop=True)
    return console.export_svg(title=title, unique_id="vibe", theme=theme)
