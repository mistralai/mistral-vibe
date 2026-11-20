"""/skills opens while a turn runs, and a switch the turn holds says it applies when the turn ends."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline
from e2e.app_server.skills import METHODS, installed, skill

handshake = {
    "skills/installed": [
        installed([skill("lint")]),
        installed([skill("lint", enabled=False)]),
    ],
    "skills/setEnabled": {"status": "pending"},
}
request_methods = METHODS

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first"),
    assistant_msg("Working on it."),
    {"release": 4},
    "/skills\r",
    " ",
]

screen_contains = ("○ lint", "Saved; it applies when the current turn ends.")
screen_excludes = ("Slash commands cannot be queued",)
