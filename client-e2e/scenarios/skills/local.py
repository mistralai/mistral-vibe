"""Without registry skills there are no tabs and no Shared skills; Enter shows a local skill's switch and body, and Esc steps back to the list."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, installed, skill

handshake = {
    "skills/installed": installed([
        skill("code-review", description="Review a diff for bugs"),
        skill("lint", scope="project", enabled=False),
        skill("pdf", source="plugin", locked=True),
    ])
}
request_methods = METHODS

_ESCAPE = "\x1b[27u"

timeline: Timeline = ["/skills\r", "\r", _ESCAPE]

screen_contains = ("code-review",)
screen_excludes = ("My Skills", "Shared", "switch tab")
