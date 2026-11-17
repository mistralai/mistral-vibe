"""`/raw` shows a streaming agent message as its Markdown source, deltas after the toggle included."""

from __future__ import annotations

from e2e.app_server.events import assistant_delta, assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "stream a list"
_ID = "raw-stream"

screen_contains = ("Generating", "## Steps", "- **first**", "- `second`", "> third")

# The batch is turn start, queue drain, user row, assistant row, two deltas, then the post-toggle delta.
timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    assistant_msg("## Steps\n\n", entry_id=_ID, generation_status="in_progress"),
    assistant_delta(_ID, "- **first**\n"),
    assistant_delta(_ID, "- `second`\n"),
    assistant_delta(_ID, "> third"),
    {"release": 6},
    "/raw\r",
    {"release": 1},
]
