"""`/` focuses the search, typing filters the open tab and each tab counts its matches, then Esc leaves the field, clears the filter, and closes."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import (
    METHODS,
    installed,
    registry_on,
    sample_catalog,
    sample_skills,
)

handshake = {
    **registry_on(),
    "skills/installed": installed(sample_skills()),
    "skills/catalog": sample_catalog(),
}
request_methods = METHODS

_ESCAPE = "\x1b[27u"

timeline: Timeline = ["/skills\r", "/tr", _ESCAPE, _ESCAPE, _ESCAPE]

screen_excludes = ("Search skills",)
