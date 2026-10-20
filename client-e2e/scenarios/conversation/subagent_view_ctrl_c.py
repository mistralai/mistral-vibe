"""Ctrl+C in a read-only child view warns once and arms the quit, without refetching history."""

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
_TASK = "find the grouping code"
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

request_methods = frozenset({"session/read"})

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

screen_contains = (
    "Viewing Explore 1 · read-only · Esc to return",
    _CHILD_RESULT,
    "You can't interact with a subagent directly. Return to Main conversation and ask the main agent to stop it.",
    "Press Ctrl+C again to quit",
)
screen_excludes = (_PROMPT,)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent(_TASK),
    child_session_updated(),
    turn_completed(),
    {"release_after": 6},
    "\x1b[B",  # Down — focus the list on the Main row
    "\x1b[B",  # Down — highlight the child row
    "\r",  # Enter — open the read-only child view
    "\x03",  # Ctrl+C — the read-only warning, and the quit armed
]
