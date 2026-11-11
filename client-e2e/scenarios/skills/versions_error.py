"""A shared skill whose versions cannot be read says so and leaves the list in place."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import (
    METHODS,
    catalog,
    detail,
    entry,
    installed,
    registry_on,
)

handshake = {
    **registry_on(),
    "skills/installed": installed([]),
    "skills/catalog": catalog([entry("translate", 4)]),
    "skills/detail": detail("# translate\n\nTranslate the docs folder."),
    "skills/versions": {
        "error": {"code": "internal_error", "message": "Registry down"}
    },
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\t", "\r"]

screen_contains = ("Failed to read the versions of translate", "Shared")
screen_excludes = ("not in My Skills",)
