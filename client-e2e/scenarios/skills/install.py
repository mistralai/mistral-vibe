"""A shared skill's page adds it at latest into Global with one Enter on Add to My Skills, then becomes its installed page."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import (
    APPLIED,
    METHODS,
    catalog,
    detail,
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
    "skills/catalog": catalog([entry("translate", 4, description="Translate docs")]),
    "skills/versions": versions(4, 3),
    "skills/detail": detail("# translate\n\nTranslate the docs folder."),
    "skills/import": APPLIED,
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\t", "\r", "jj", "\r"]

screen_contains = ("Remove",)
screen_excludes = ("not in My Skills",)
