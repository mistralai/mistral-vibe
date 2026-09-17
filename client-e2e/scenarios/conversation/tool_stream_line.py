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
# Step 0 lands on the accepted-before-started transient, where Python counts the
# in-flight prompt as queued and Rust shows turn-interrupt controls instead
# (see queue/first_prompt_hint.py); the streamed-line behavior under test is
# steps 1 and 2.
capture_steps = {1, 2}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash_started("tail -f app.log", _EFFECT_ID),
    bash_output_chunk(_EFFECT_ID, "downloading 42%"),
    bash_output_chunk(_EFFECT_ID, "... 99%"),
    bash_completed(_EFFECT_ID, "tail -f app.log", stdout="done"),
    assistant_msg("Done."),
    turn_completed(),
    # The first six events (turn start plus the queue drain the harness adds,
    # then the call and its two streamed chunks) pin the live `→` line on the
    # LATEST chunk, never the accumulated output.
    {"release": 6},
    # The remaining three settle the effect: the `→` line clears.
    {"release": 3},
]
