"""d on a shared skill's row asks again in the row, and a second d removes it from every scope."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import (
    APPLIED,
    METHODS,
    catalog,
    installed,
    registry_on,
    skill,
)

_PROJECT = skill("deploy", source="registry", scope="project", version=3)
_GLOBAL = skill("deploy", source="registry", version=2, active=False)
_LINT = skill("lint")

handshake = {
    **registry_on(),
    "skills/installed": [installed([_PROJECT, _GLOBAL, _LINT]), installed([_LINT])],
    "skills/catalog": catalog(),
    "skills/remove": APPLIED,
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "d", "d"]

screen_contains = ("lint",)
screen_excludes = ("deploy",)
