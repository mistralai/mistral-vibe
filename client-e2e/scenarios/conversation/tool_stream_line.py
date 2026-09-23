"""A running tool's streamed output shows the arrow line and clears on settle."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash_completed,
    bash_output_chunk,
    bash_started,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "tail the log"
_EFFECT_ID = "streaming-bash"

# Busy frames settle under replay so the streamed arrow line is capturable.
env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}
# A release step must not consume a marker of its own, so settle key by key.
settle_per_key = True
# Open the busy group before capturing the streamed output and its removal.
_EXPAND_GROUP = "\x1b[<0;1;32M\x1b[<0;1;32m"
capture_steps = {2, 3, 4}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash_started("tail -f app.log", _EFFECT_ID),
    bash_output_chunk(_EFFECT_ID, "downloading 42%"),
    bash_output_chunk(
        _EFFECT_ID,
        "old\r\x1b[31m... 99%\x1b[0m\x1b]0;hidden\x07\x00\x08\x7f\r\n\tfinishing",
    ),
    bash_output_chunk(_EFFECT_ID, "\x1b[2J\x1b[H\x07\x00\x08\x7f"),
    bash_completed(_EFFECT_ID, "tail -f app.log", stdout="done"),
    assistant_msg("Done."),
    turn_completed(),
    # The first six events (turn start plus the queue drain the harness adds,
    # then the call and its two streamed chunks) pin the live `→` line on the
    # LATEST chunk, never the accumulated output.
    {"release": 6},
    _EXPAND_GROUP,
    # A controls-only chunk has no visible stream row.
    {"release": 1},
    # The remaining three settle the effect.
    {"release": 3},
]
