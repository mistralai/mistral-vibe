"""Unpin from Global in a skill pinned in both scopes removes only that pin; the page stays on the Project one."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
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

_SHADOWED = skill("deploy", source="registry", version=2, active=False)
_PROJECT = skill("deploy", source="registry", scope="project", version=2)

handshake = {
    **registry_on(),
    "skills/installed": [installed([_PROJECT, _SHADOWED]), installed([_PROJECT])],
    "skills/catalog": catalog(),
    "skills/versions": versions(2, 1),
    "skills/detail": detail("# deploy\n\nShip the release."),
    "skills/remove": APPLIED,
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\r", "\r", "jj", "\r"]

screen_contains = ("not pinned",)
