"""The startup picker holds the positional prompt; Esc drops it and the composer sends the next one."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"

# The startup frame shows the picker with no turn started; only the typed prompt is enqueued.
client_args = ("continue the work", "--resume")
timeline: Timeline = [
    _ESCAPE,
    "hi\r",
    turn_started(),
    user_msg("hi"),
    assistant_msg("Reply to the typed prompt."),
    turn_completed(),
]

screen_contains = ("Reply to the typed prompt.",)
screen_excludes = ("continue the work", "Resume cancelled.")
