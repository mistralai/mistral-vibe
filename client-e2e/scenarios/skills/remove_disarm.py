"""Esc disarms an armed removal and keeps the filter, and a press on the search row disarms it too."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, catalog, installed, registry_on, skill
from e2e.pty.mouse import click

handshake = {
    **registry_on(),
    "skills/installed": installed([skill("deploy", source="registry", version=2)]),
    "skills/catalog": catalog(),
}
request_methods = METHODS

_PRESS_SEARCH = click(10, 23)

timeline: Timeline = ["/skills\r", "/dep", "\x1b", "d", "\x1b", "d", _PRESS_SEARCH]

screen_contains = ("dep", "leave search")
screen_excludes = ("Press d again",)
