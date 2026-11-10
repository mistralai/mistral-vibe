"""`d` on a local row leaves the registry pin it shadows, and a shared skill whose every pin is shadowed stays under Shared, its page showing the pin and Remove."""

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
    versions,
)

handshake = {
    **registry_on(),
    "skills/installed": installed([
        skill("deploy"),
        skill("deploy", source="registry", version=2, active=False),
    ]),
    "skills/catalog": catalog([entry("deploy", 2)], project=False),
    "skills/detail": detail("# deploy\n\nShip the release."),
    "skills/versions": versions(2, 1),
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "dd", "\t", "\r"]

screen_contains = ("v2  pinned  (shadowed)", "Remove")
screen_excludes = ("Enabled",)
