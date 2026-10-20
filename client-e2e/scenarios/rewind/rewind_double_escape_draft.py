"""Double Escape with a draft clears the composer and never enters rewind mode."""

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
    "unsent draft",
    _ESCAPE * 2,
]

handshake = {"session/rewind/read": {"hasFileChanges": False, "paths": []}}
request_methods = {"session/rewind/read"}

capture_steps = {1, 2}
screen_excludes = ("unsent draft", "Edit message from here")
