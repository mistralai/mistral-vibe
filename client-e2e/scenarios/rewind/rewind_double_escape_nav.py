"""Double Escape opens rewind on the last prompt; ←/→ and action-step Esc walk prompts; q on the persistence step exits."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"

timeline: Timeline = [
    "hi\r",
    turn_started(),
    user_msg("hi", entry_id="user-hi"),
    assistant_msg("Hi. What do you need?"),
    turn_completed(),
    "again\r",
    turn_started(),
    user_msg("again", entry_id="user-again"),
    assistant_msg("Here we go again."),
    turn_completed(),
    _ESCAPE * 2,
    "\x1b[D",
    "\x1b[C",
    _ESCAPE,
    "\r",
    "q",
]

handshake = {"session/rewind/read": {"hasFileChanges": False, "paths": []}}
request_methods = {"session/rewind/read", "session/rewind"}

capture_steps = {2, 3, 4, 5, 6, 7}
screen_excludes = ("Keep in current session", "Edit message from here")
