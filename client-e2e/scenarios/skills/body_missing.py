"""A shared skill whose body the registry does not return says so under its page's rows, and the page still installs."""

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

handshake = {
    **registry_on(),
    "skills/installed": [
        installed([]),
        installed([skill("translate", source="registry", version=4, alias="latest")]),
    ],
    "skills/catalog": catalog([entry("translate", 4, "workspace", "Translate docs")]),
    "skills/versions": versions(4),
    "skills/detail": {"detail": None, "body": None},
    "skills/import": APPLIED,
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\t", "\r", "jj", "\r"]

screen_contains = ("Remove",)
