"""r on a shared skill's page unpins it at once, the page gives way to the list once no pin is left, and a notice says how to add it back."""

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

timeline: Timeline = ["/skills\r", "\r", "r"]

screen_contains = ("lint", "Removed deploy from My Skills. Add it again from Shared.")
screen_excludes = ("● deploy",)
