"""A skill's page shows the highlighted scope's own pin under its rows, so each scope row previews what that scope pins."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import (
    METHODS,
    catalog,
    installed,
    registry_on,
    skill,
    versions,
)

_PROJECT = skill(
    "deploy",
    source="registry",
    scope="project",
    version=3,
    prompt="# deploy\n\nProject pin: ship from the release branch.",
)
_GLOBAL = skill(
    "deploy",
    source="registry",
    version=2,
    active=False,
    prompt="# deploy\n\nGlobal pin: ship from main.",
)

handshake = {
    **registry_on(),
    "skills/installed": installed([_PROJECT, _GLOBAL]),
    "skills/catalog": catalog(),
    "skills/versions": versions(3, 2, 1),
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\r", "j"]

screen_contains = ("Project pin: ship from the release branch.",)
screen_excludes = ("Global pin: ship from main.",)
