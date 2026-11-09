"""The copy icon after the footer's cwd: hovering highlights it and a click copies the whole path."""

from __future__ import annotations

from e2e.app_server.config import COLUMNS, ROWS
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}

_LEFT = "/test/workdir ⧉"
_RIGHT = "ctx        0/600k"
screen_rows = {ROWS - 1: _LEFT.ljust(COLUMNS - len(_RIGHT)) + _RIGHT}

# SGR mouse coordinates are 1-indexed; the icon is the cell after the cwd and its space.
_ICON_COL = len(_LEFT)
_HOVER = f"\x1b[<35;{_ICON_COL};{ROWS}M"
_CLICK = f"\x1b[<0;{_ICON_COL};{ROWS}M\x1b[<0;{_ICON_COL};{ROWS}m"

expected_clipboard = "/test/workdir"
screen_contains = ("Copied",)

# The hover frame paints the icon's highlight before the click copies.
timeline: Timeline = [_HOVER, _CLICK]
