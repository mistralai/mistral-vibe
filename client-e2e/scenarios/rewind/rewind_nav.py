from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

skip = (
    "Python repaints the rewind highlight one step late: its selection worker is "
    "not awaited before the frame settles, so ←/→ lag by one capture."
)

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
    "again\r",
    turn_started(),
    user_msg("again"),
    assistant_msg("Here we go again."),
    turn_completed(),
    "/rewind\r",
    "\x1b[D",
    "\x1b[C",
    "\x1b[B",
    "q",
]

handshake = {"session/rewind/read": {"hasFileChanges": False, "paths": []}}
