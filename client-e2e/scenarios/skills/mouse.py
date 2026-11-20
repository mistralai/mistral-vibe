"""A click on the highlighted row opens its page, a click on a scope row opens its picker, and a click on a version pins it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, advance
from e2e.app_server.skills import (
    APPLIED,
    METHODS,
    catalog,
    detail,
    installed,
    registry_on,
    skill,
    versions,
)
from e2e.pty.mouse import click

_PINNED = skill("deploy", source="registry", version=2)
_SHADOWED = skill("deploy", source="registry", version=2, active=False)
_PROJECT = skill("deploy", source="registry", scope="project", version=1)

handshake = {
    **registry_on(),
    "skills/installed": [installed([_PINNED]), installed([_PROJECT, _SHADOWED])],
    "skills/catalog": catalog(),
    "skills/versions": versions(2, 1),
    "skills/detail": detail("# deploy\n\nShip the release."),
    "skills/import": APPLIED,
}
request_methods = METHODS

_OPEN_DEPLOY = click(10, 27)
_PICK_PROJECT = click(10, 26)
_PIN_V1 = click(10, 27)

timeline: Timeline = [
    "/skills\r",
    _OPEN_DEPLOY,
    advance(600),
    _PICK_PROJECT,
    advance(600),
    _PIN_V1,
]

screen_contains = ("v1  pinned", "(shadowed)")
