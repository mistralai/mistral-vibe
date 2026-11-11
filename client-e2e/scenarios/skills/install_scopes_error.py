"""Add to My Skills across both scopes stops at the first import the server refuses, reports it and re-reads the pins."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import (
    METHODS,
    catalog,
    detail,
    entry,
    installed,
    registry_on,
    versions,
)

handshake = {
    **registry_on(),
    "skills/installed": installed([]),
    "skills/catalog": catalog([entry("translate", 4, description="Translate docs")]),
    "skills/versions": versions(4, 3),
    "skills/detail": detail("# translate\n\nTranslate the docs folder."),
    "skills/import": {"error": {"code": "invalid_params", "message": "Registry down"}},
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\t", "\r", "j\r", "jj\r", "j", "\r"]

screen_contains = ("Adding translate failed: Registry down", "not in My Skills")
