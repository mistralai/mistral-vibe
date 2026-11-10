"""A second click on a list row opens its page even though the first click previewed a longer body under the rows."""

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
    versions,
)
from e2e.pty.mouse import click

handshake = {
    **registry_on(),
    "skills/installed": installed([skill("lint")]),
    "skills/catalog": catalog([
        entry("changelog", 2, "workspace"),
        entry("translate", 4, "workspace", "Translate docs"),
    ]),
    "skills/versions": versions(4, 3),
    "skills/detail": [
        detail("# changelog"),
        detail("# translate\n\nTranslate the docs folder."),
    ],
}
request_methods = METHODS

_CLICK_TRANSLATE = click(10, 26)

timeline: Timeline = ["/skills\r", "\t", _CLICK_TRANSLATE, _CLICK_TRANSLATE]

screen_contains = ("translate  not in My Skills", "Add to My Skills")
