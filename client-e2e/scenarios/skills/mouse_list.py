"""A first click on a row of the list only highlights it, and a press on the search row focuses the field."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, catalog, installed, registry_on, skill
from e2e.pty.mouse import click

handshake = {
    **registry_on(),
    "skills/installed": installed([
        skill("lint"),
        skill("deploy", source="registry", version=2),
    ]),
    "skills/catalog": catalog(),
}
request_methods = METHODS

_HIGHLIGHT_DEPLOY = click(10, 26)
_FOCUS_SEARCH = click(10, 22)

timeline: Timeline = ["/skills\r", _HIGHLIGHT_DEPLOY, _FOCUS_SEARCH]

screen_contains = ("leave search",)
screen_excludes = ("Esc back",)
