"""After Esc, /retry replaces the unfinished answer and the interrupt marker with a full reply."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    discard_entry,
    turn_completed,
    turn_interrupted,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# The server settles the cut-off answer as discarded, then closes the interrupted turn.
on_request = {
    "turn/interrupt": [discard_entry("partial", cause="interrupt"), turn_interrupted()]
}

screen_contains = ("Here is the full answer.",)
screen_excludes = ("Let me che", "Interrupted", "/retry")

timeline: Timeline = [
    "hello\r",
    turn_started(),
    user_msg("hello"),
    assistant_msg("Let me che", entry_id="partial", generation_status="in_progress"),
    # Frame: the answer is still streaming.
    {"release": 4},
    "\x1b",
    "/retry\r",
    turn_started(),
    assistant_msg("Here is the full answer."),
    turn_completed(),
    {"release": 4},
]
