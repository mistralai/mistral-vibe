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

# The child transcript reads its own session: the replay server answers the
# per-session pin `session/read#<child id>` (see replay.rs).
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

# The viewed child stays Running after the parent turn: freeze the loading
# gradient so the idle capture cannot catch it mid-wipe on a slow host.
env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

screen_contains = {
    "rust": ("Main conversation", "Explore (Explore 1) [running] · 0 tokens")
}
screen_excludes = {"rust": ("Loading subagent transcript…",)}

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent(_TASK),
    child_session_updated(),
    turn_completed(),
    # Gate the batch behind settles (5 events plus the injected queue drain):
    # ungated, its events race the idle marker that follows the submit.
    {"release_after": 6},
    "\x1b[B",  # Down — focus the list on the Main row
    "\x1b[B",  # Down — highlight the child row
    "\r",  # Enter — open the read-only child view (Loading, then history)
    "\x1b",  # Esc — return to the main conversation
]
