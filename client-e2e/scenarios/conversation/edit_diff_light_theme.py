from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    edit_file,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_THEME": "atom-one-light"}
handshake = {
    "config/read": {"config": {"theme": "atom-one-light"}},
    "runtime/read": {"runtime": {"config": {"theme": "atom-one-light"}}},
}

_PROMPT = "edit the file"
_OLD = "alpha\nbeta\ngamma\ndelta\n"
_NEW = "alpha\nBETA\ngamma\ndelta\n"
timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    edit_file("notes.txt", [(3, _OLD, _NEW)]),
    assistant_msg("Done."),
    turn_completed(),
]
