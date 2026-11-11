"""Before adding, each scope takes its own choice: Global tracks latest, Project pins v3, and Add to My Skills imports both."""

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

_GLOBAL = skill("translate", source="registry", version=4, alias="latest", active=False)
_PROJECT = skill("translate", source="registry", scope="project", version=3)

handshake = {
    **registry_on(),
    "skills/installed": [installed([]), installed([_PROJECT, _GLOBAL])],
    "skills/catalog": catalog([entry("translate", 4, description="Translate docs")]),
    "skills/versions": versions(4, 3),
    "skills/detail": detail("# translate\n\nTranslate the docs folder."),
    "skills/import": APPLIED,
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\t", "\r", "j\r", "jj\r", "j", "\r"]

screen_contains = ("Remove",)
screen_excludes = ("not in My Skills",)
