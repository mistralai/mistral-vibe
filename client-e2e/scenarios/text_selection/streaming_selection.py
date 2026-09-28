"""Keep a transcript selection attached while an assistant message streams."""

from __future__ import annotations

from e2e.app_server.events import assistant_delta, assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0", "VIBE_REPLAY_SETTLE_BUSY": "1"}
capture_startup = False
capture_steps = {1, 2, 3}

_ASSISTANT_ID = "streaming-assistant"
_INITIAL = "\n\n".join(
    f"The quick brown fox jumps over the lazy dog on line {i:02d}."
    for i in range(1, 24)
)
_DELTA = "\n\nThe stream keeps growing after this selected line."

_PRESS = "\x1b[<0;12;20M"
_DRAG = "\x1b[<32;48;20M"
_RELEASE = "\x1b[<0;48;20m"

timeline: Timeline = [
    "tell me a story\r",
    turn_started(),
    user_msg("tell me a story"),
    assistant_msg(_INITIAL, entry_id=_ASSISTANT_ID, generation_status="in_progress"),
    assistant_delta(_ASSISTANT_ID, _DELTA),
    {"release": 3},
    _PRESS + _DRAG + _RELEASE,
    {"release": 1},
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
