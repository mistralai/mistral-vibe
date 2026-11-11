"""Remove on a skill's page asks for Enter again, then unpins its global pin, and the page gives way to the list once no pin is left."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import (
    APPLIED,
    METHODS,
    catalog,
    installed,
    registry_on,
    skill,
    versions,
)

_DEPLOY = skill("deploy", source="registry", version=2)

handshake = {
    **registry_on(),
    "skills/installed": [
        installed([_DEPLOY, skill("lint")]),
        installed([skill("lint")]),
    ],
    "skills/catalog": catalog(),
    "skills/versions": versions(2),
    "skills/remove": APPLIED,
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\r", "jjj", "\r", "\r"]

screen_contains = ("lint",)
screen_excludes = ("deploy",)
