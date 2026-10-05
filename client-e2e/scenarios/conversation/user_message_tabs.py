"""A submitted prompt keeps its pasted tabs, expanded to 8-column stops like Textual."""

from __future__ import annotations

from e2e.app_server.events import paste, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "build:\n\tcargo build\n\tcargo test\na\tb"

screen_contains = {
    "python": ("        cargo build", "a       b"),
    "rust": ("        cargo build", "a       b"),
}

timeline: Timeline = [
    paste(_PROMPT),
    "\r",
    turn_started(),
    user_msg(_PROMPT),
    turn_completed(),
]
