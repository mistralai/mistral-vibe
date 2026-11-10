"""A box at least 140 columns wide previews the highlighted skill beside the list; a narrower one keeps it under the rows."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline, resize, take_snapshot
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
    "skills/installed": installed([
        skill("lint", prompt="# lint\n\nCheck the style before every commit."),
        skill("deploy", source="registry", version=2),
    ]),
    "skills/catalog": catalog([entry("translate", 4, "workspace", "Translate docs")]),
    "skills/detail": detail("# translate\n\nTranslate the docs folder."),
}
request_methods = METHODS

timeline: Timeline = [
    "/skills\r",
    resize(40, 160),
    "\t",
    take_snapshot(),
    resize(40, 120),
    take_snapshot(),
]

screen_contains = ("Translate the docs folder.",)
