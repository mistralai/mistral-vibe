"""The only pin can never be unpinned; an unpinned Project row's picker starts on Global's v2, the version under it pins there, and the global pin turns shadowed."""

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

_GLOBAL = skill("deploy", source="registry", version=2)
_SHADOWED = skill("deploy", source="registry", version=2, active=False)
_PROJECT = skill("deploy", source="registry", scope="project", version=1)

handshake = {
    **registry_on(),
    "skills/installed": [installed([_GLOBAL]), installed([_PROJECT, _SHADOWED])],
    "skills/catalog": catalog(),
    "skills/versions": versions(2, 1),
    "skills/detail": detail("# deploy\n\nShip the release."),
    "skills/import": APPLIED,
}
request_methods = METHODS

_ESCAPE = "\x1b[27u"

timeline: Timeline = ["/skills\r", "\r", "\r", _ESCAPE, "j\r", "j\r"]

screen_contains = ("(shadowed)", "Project  v1  pinned")
