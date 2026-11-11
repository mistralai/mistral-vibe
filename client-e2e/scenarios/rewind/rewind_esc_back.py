from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline, take_snapshot

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
    "/rewind\r",
    "1",
    "\x1b",
    "q",
    take_snapshot(),
]

handshake = {"session/rewind/read": {"hasFileChanges": False, "paths": []}}
