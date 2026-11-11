"""The footer's cwd gets a copy icon only once shortened: hovering shows the hand and a click copies the whole path."""

from __future__ import annotations

from e2e.app_server.config import ROWS
from e2e.app_server.scenario import Timeline, resize, take_snapshot

env = {"SSH_TTY": "/dev/pts/0"}

_COLUMNS = 20
_LEFT = "…/workdir ⧉"
_RIGHT = "ctx   0%"
screen_rows = {ROWS - 1: _LEFT.ljust(_COLUMNS - len(_RIGHT)) + _RIGHT}

# SGR mouse coordinates are 1-indexed; the icon is the cell after the cwd and its space.
_ICON_COL = len(_LEFT)
_HOVER = f"\x1b[<35;{_ICON_COL};{ROWS}M"
_CLICK = f"\x1b[<0;{_ICON_COL};{ROWS}M\x1b[<0;{_ICON_COL};{ROWS}m"

expected_clipboard = "/test/workdir"
screen_contains = ("Copied",)

# The full-width frame has no icon; narrowing shortens the cwd and paints it, then hover and click.
timeline: Timeline = [
    resize(ROWS, _COLUMNS),
    take_snapshot(),
    _HOVER,
    take_snapshot(),
    _CLICK,
    take_snapshot(),
]
