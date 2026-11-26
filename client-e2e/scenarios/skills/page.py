"""Enter opens a pinned skill's page, a row per scope; Enter on Project opens its picker on the alias in effect, previewing that version; Esc steps back each level."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import (
    METHODS,
    catalog,
    detail,
    installed,
    registry_on,
    skill,
    versions,
)

_PIN = skill("deploy", source="registry", scope="project", version=2, alias="stable")

handshake = {
    **registry_on(),
    "skills/installed": installed([_PIN]),
    "skills/catalog": catalog(),
    "skills/versions": versions(3, 2, 1, aliases={2: ["stable"]}),
    "skills/detail": detail("# deploy v2\n\nShip the tagged release."),
}
request_methods = METHODS

_ESCAPE = "\x1b[27u"

timeline: Timeline = ["/skills\r", "\r", "j\r", _ESCAPE, _ESCAPE, _ESCAPE]

screen_excludes = ("Search skills",)
