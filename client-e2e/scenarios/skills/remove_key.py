"""r on a shared skill's row removes it from every scope at once: it is easy to add back."""

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

timeline: Timeline = ["/skills\r", "r"]

screen_contains = ("lint",)
screen_excludes = ("● deploy",)
