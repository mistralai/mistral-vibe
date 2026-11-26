"""My Skills and Shared are tabs: Tab, Shift+Tab and ←→ switch them, and a press on a tab opens it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, catalog, entry, installed, registry_on, skill
from e2e.pty.mouse import click

handshake = {
    **registry_on(),
    "skills/installed": installed([skill("lint")]),
    "skills/catalog": catalog([entry("translate", 4, "workspace", "Translate docs")]),
}
request_methods = METHODS

_PRESS_SHARED = click(27, 20)
_SHIFT_TAB = "\x1b[Z"
_RIGHT = "\x1b[C"
_LEFT = "\x1b[D"

timeline: Timeline = [
    "/skills\r",
    "\t",
    "\t",
    _SHIFT_TAB,
    _SHIFT_TAB,
    _RIGHT,
    _LEFT,
    _PRESS_SHARED,
]

screen_contains = ("translate", " Shared 1 ", "Tab/←→ switch tab")
