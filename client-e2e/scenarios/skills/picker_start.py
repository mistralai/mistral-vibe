"""A scope's version picker starts on the version the other scope pins, so Enter right away pins that version there too."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import (
    APPLIED,
    METHODS,
    catalog,
    detail,
    installed,
    registry_on,
    skill,
    versions,
)

_GLOBAL = skill("deploy", source="registry", version=1)
_SHADOWED = skill("deploy", source="registry", version=1, active=False)
_PROJECT = skill("deploy", source="registry", scope="project", version=1)

handshake = {
    **registry_on(),
    "skills/installed": [installed([_GLOBAL]), installed([_PROJECT, _SHADOWED])],
    "skills/catalog": catalog(),
    "skills/versions": versions(3, 2, 1),
    "skills/detail": detail("# deploy\n\nVersion 1 ships from main."),
    "skills/import": APPLIED,
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", "\r", "j\r", "\r"]

screen_contains = ("Project  v1  pinned",)
