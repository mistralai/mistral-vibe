"""The list previews the highlighted skill under its rows: an installed skill's own body, and a shared skill's description over its newest body."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import (
    METHODS,
    catalog,
    detail,
    entry,
    installed,
    registry_on,
    skill,
)

handshake = {
    **registry_on(),
    "skills/installed": installed([skill("lint")]),
    "skills/catalog": catalog([entry("translate", 4, "workspace", "Translate docs")]),
    "skills/detail": detail("# translate\n\nTranslate the docs folder."),
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\t"]

screen_contains = ("Translate docs", "Translate the docs folder.")
