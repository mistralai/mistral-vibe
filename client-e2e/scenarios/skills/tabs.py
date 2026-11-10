"""My Skills and Shared are tabs: Tab switches them, and a press on a tab opens it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import (
    METHODS,
    catalog,
    detail,
    entry,
    installed,
    registry_on,
    skill,
)
from e2e.pty.mouse import click

handshake = {
    **registry_on(),
    "skills/installed": installed([skill("lint")]),
    "skills/catalog": catalog([entry("translate", 4, "workspace", "Translate docs")]),
    "skills/detail": detail("# translate\n\nTranslate the docs folder."),
}
request_methods = METHODS

_PRESS_SHARED = click(27, 22)

timeline: Timeline = ["/skills\r", "\t", "\t", _PRESS_SHARED]

screen_contains = ("translate", "[Shared]")
