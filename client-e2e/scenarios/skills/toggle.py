"""Space turns the highlighted skill off with skills/setEnabled, then the re-read pins redraw it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import APPLIED, METHODS, installed, skill

handshake = {
    "skills/installed": [
        installed([skill("code-review"), skill("lint")]),
        installed([skill("code-review", enabled=False), skill("lint")]),
    ],
    "skills/setEnabled": APPLIED,
}
request_methods = METHODS

timeline: Timeline = ["/skills\r", " "]

screen_contains = ("○ code-review",)
