"""An install that lands shadowed by a local file of the same name keeps its page, and Esc returns to its Shared row."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import (
    APPLIED,
    METHODS,
    catalog,
    entry,
    installed,
    registry_on,
    skill,
    versions,
)

_LOCAL = skill("deploy")
_SHADOWED = skill("deploy", source="registry", version=2, alias="latest", active=False)

handshake = {
    **registry_on(),
    "skills/installed": [installed([_LOCAL]), installed([_LOCAL, _SHADOWED])],
    "skills/catalog": catalog([entry("deploy", 2)]),
    "skills/versions": versions(2, 1),
    "skills/detail": {"detail": None, "body": "# deploy"},
    "skills/import": APPLIED,
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\t\r", "jj\r", "\x1b"]

screen_contains = ("Enter view", "Shared")
