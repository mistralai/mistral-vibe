"""/skills lists installed skills one row per name under My Skills and the shared skills to add under Shared, with columns, an update badge, wrapping j/k, and Tab between the tabs."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import (
    METHODS,
    detail,
    installed,
    registry_on,
    sample_catalog,
    sample_skills,
)

handshake = {
    **registry_on(),
    "skills/installed": installed(sample_skills()),
    "skills/catalog": sample_catalog(),
    "skills/detail": detail("# changelog\n\nCollect the release notes."),
}
request_methods = METHODS

_ESCAPE = "\x1b[27u"

timeline: Timeline = ["/skills\r", "k", "j", "\t", _ESCAPE]

screen_excludes = ("Search skills",)
