"""On an installed skill, a scope's version picker previews each version it lands on, writes the pick at once with skills/import, and returns to the scope's row."""

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

handshake = {
    **registry_on(),
    "skills/installed": [
        installed([skill("deploy", source="registry", version=3)]),
        installed([skill("deploy", source="registry", version=1)]),
    ],
    "skills/catalog": catalog(),
    "skills/versions": versions(3, 2, 1),
    "skills/detail": [
        detail("# deploy v3\n\nShip the release."),
        detail("# deploy v2\n\nShip the release, then tag it."),
        detail("# deploy v1\n\nShip it by hand."),
    ],
    "skills/import": APPLIED,
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\r", "\r", "jj", "\r"]

screen_contains = ("v1  pinned",)
screen_excludes = ("Action failed",)
