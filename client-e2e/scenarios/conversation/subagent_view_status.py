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
                {
                    "id": "child-turn-start",
                    "type": "message",
                    "role": "user",
                    "source": "turn_start",
                    "content": [{"type": "text", "text": _TASK}],
                    "generationStatus": "completed",
                    "createdAt": 1_787_593_260_000,
                    "updatedAt": 1_787_593_260_000,
                },
                _CHILD_SESSION_ID,
            ),
            child_entry(
                assistant_msg(_CHILD_RESULT)["params"]["entry"], _CHILD_SESSION_ID
            ),
        ],
    )
}

request_methods = frozenset({"session/read"})

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

screen_contains = {
    "rust": (
        "Viewing Explore 1 · read-only · Esc to return",
        "This subagent is ready for a new instruction. Return to Main conversation and ask the main agent to give it a new",
        _CHILD_RESULT,
    )
}
screen_excludes = {"rust": ("Waiting for input",)}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent(_TASK),
    child_session_updated(status="blocked"),
    turn_completed(),
    child_session_updated(status="idle"),
    {"release": 5},  # the running turn plus the blocked child; the rest held
    "\x1b[B",  # Down — focus the list on the Main row
    "\x1b[B",  # Down — highlight the blocked child row
    "\r",  # Enter — open the view: Waiting for input, then the child history
    {"release": 2},  # turn completes; the child goes idle: ready message
]
