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

_PROMPT = "delegate to the worker"
_TASK = "summarize the diff"
_CHILD_SESSION_ID = "child-session"

handshake = {
    f"session/read#{_CHILD_SESSION_ID}": child_history_state(
        _CHILD_SESSION_ID,
        [
            child_entry(
                assistant_msg("The diff renames one module.")["params"]["entry"],
                _CHILD_SESSION_ID,
            )
        ],
    )
}

request_methods = frozenset({"session/read"})

# The child view's banner names the model the child's agent type selects, not the session's.
screen_contains = ("Viewing Explore 1 · read-only · Esc to return", "deepseek[off]")
screen_excludes = ("mistral-medium-3.5",)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    subagent(_TASK),
    child_session_updated(model={"displayName": "deepseek", "thinking": "off"}),
    turn_completed(),
    {"release_after": 6},
    "\x1b[B",  # Down — focus the list on the Main row
    "\x1b[B",  # Down — highlight the child row
    "\r",  # Enter — open the read-only child view
]
