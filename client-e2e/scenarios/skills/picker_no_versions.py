"""A picker with no published versions keeps its body pane in place when the highlight moves from latest, which previews nothing, to Unpin."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import (
    METHODS,
    catalog,
    detail,
    installed,
    registry_on,
    skill,
    versions,
)

_PROJECT = skill("deploy", source="registry", scope="project", version=1)
_GLOBAL = skill("deploy", source="registry", version=2, active=False)

handshake = {
    **registry_on(),
    "skills/installed": installed([_PROJECT, _GLOBAL]),
    "skills/catalog": catalog(),
    "skills/versions": versions(),
    "skills/detail": detail("# deploy\n\nShip the release."),
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\r", "\r", "j"]

screen_contains = ("Unpin from Global", "Ship the release.")
