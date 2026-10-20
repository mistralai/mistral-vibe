"""A child view shows the child's own loader and context usage, and Esc restores the main ones."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    child_entry,
    child_history_state,
    child_session_updated,
    subagent,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "delegate the investigation"
_CHILD_RESULT = "Found the grouping code in transcript/grouping.rs."
_CHILD_SESSION_ID = "child-session"

handshake = {
    f"session/read#{_CHILD_SESSION_ID}": child_history_state(
        _CHILD_SESSION_ID,
        [
            child_entry(
                assistant_msg(_CHILD_RESULT)["params"]["entry"], _CHILD_SESSION_ID
            )
        ],
    )
}

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

# Final frame: back on Main, the main loader and the main context gauge return.
screen_contains = ("Generating", "0/600k tokens (0%)")
screen_excludes = ("Viewing Explore 1", "18k/600k", "12.5k/600k")

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent("find the grouping code"),
    child_session_updated(context_tokens=12_500),
    child_session_updated("idle", context_tokens=18_000),
    turn_completed(),
    {"release": 5},  # the running turn plus the running child; the rest held
    "\x1b[B",  # Down — focus the list on the Main row
    "\x1b[B",  # Down — highlight the child row
    "\r",  # Enter — the child's Running loader and 12.5k context replace the main ones
    {
        "release": 1
    },  # the viewed child goes idle at 18k: its loader hides, the gauge follows
    "\x1b",  # Esc — back on Main: the main Generating loader and context return
]
