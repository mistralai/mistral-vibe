"""Streaming output never snaps a selection drag back to the bottom; following resumes once back there."""

from __future__ import annotations

from e2e.app_server.events import assistant_delta, assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0", "VIBE_REPLAY_SETTLE_BUSY": "1"}
capture_startup = False
capture_steps = {2, 3, 4, 5, 6}

_ASSISTANT_ID = "streaming-assistant"
_LINE = "The quick brown fox jumps over the lazy dog on line {:02d}."
_INITIAL = "\n\n".join(_LINE.format(i) for i in range(1, 31))

# The batch is turn start, queue drain, user row, assistant row, then two deltas.
# Press mid-transcript, then drag onto the top row: the edge auto-scrolls up.
_PRESS = "\x1b[<0;12;20M"
_DRAG_UP = "\x1b[<32;12;1M"
_RELEASE = "\x1b[<0;12;1m"
_SHIFT_DOWN = "\x1b[1;2B"

timeline: Timeline = [
    "tell me a story\r",
    turn_started(),
    user_msg("tell me a story"),
    assistant_msg(_INITIAL, entry_id=_ASSISTANT_ID, generation_status="in_progress"),
    assistant_delta(_ASSISTANT_ID, "\n\nA line streamed during the drag."),
    assistant_delta(_ASSISTANT_ID, "\n\nA line streamed back at the bottom."),
    {"release": 4},
    _PRESS + _DRAG_UP,
    {"release": 1},
    _RELEASE,
    _SHIFT_DOWN * 20,
    {"release": 1},
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
screen_contains = ("A line streamed back at the bottom.",)
