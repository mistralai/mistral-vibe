"""A pin another name loads the same stored version as keeps its own row, marked shadowed, and its page reaches it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import (
    METHODS,
    catalog,
    installed,
    of_skill,
    registry_on,
    skill,
    versions,
)

_SHIP = skill("ship", source="registry", scope="project", version=5)
_LAUNCH = of_skill(
    skill("launch", source="registry", version=5, active=False), "skill-ship"
)

handshake = {
    **registry_on(),
    "skills/installed": installed([_SHIP, _LAUNCH]),
    "skills/catalog": catalog(),
    "skills/versions": versions(5, 4),
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "j", "\r"]

screen_contains = ("launch", "(shadowed)", "Remove")
screen_excludes = ("Enabled",)
