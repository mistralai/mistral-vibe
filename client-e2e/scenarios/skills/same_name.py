"""Two shared skills under one name: the shadowed one's page shows only its own pins and the scope the other holds, and its Remove leaves the other's under My Skills."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import (
    APPLIED,
    METHODS,
    catalog,
    detail,
    entry,
    installed,
    of_skill,
    registry_on,
    skill,
    versions,
)

_OURS = skill("review", source="registry", scope="project", version=3)
_THEIRS = of_skill(
    skill("review", source="registry", version=1, active=False), "skill-other"
)

handshake = {
    **registry_on(),
    "skills/installed": [installed([_OURS, _THEIRS]), installed([_OURS])],
    "skills/catalog": catalog([
        entry("review", 3, "workspace", "Our review"),
        {**entry("review", 1, "workspace", "Their review"), "skillId": "skill-other"},
    ]),
    "skills/detail": detail("# review\n\nTheir checklist."),
    "skills/versions": versions(1),
    "skills/remove": APPLIED,
}
request_methods = METHODS

_END = "\x1b[F"

timeline: Timeline = ["/skills\r", "\t", "\r", _END, "\r", "\r", "\t"]

screen_contains = ("review", "Project  Shared  v3")
screen_excludes = ("Their review",)
