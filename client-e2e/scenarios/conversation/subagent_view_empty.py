"""An empty child transcript shows its placeholder under the banner."""

from __future__ import annotations

from e2e.app_server.events import (
    child_history_state,
    child_session_updated,
    subagent,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import AppServerEvent, Timeline

_PROMPT = "delegate the investigation"
_TASK = "find the grouping code"
_CHILD_SESSION_ID = "child-session"

# The child session exists but has no history: opening its view shows the
# empty placeholder, which must flow under the banner (never over it). The
# spawn carries no task, so no parent instruction row is synthesized either.
handshake = {
    f"session/read#{_CHILD_SESSION_ID}": child_history_state(_CHILD_SESSION_ID, [])
}

request_methods = frozenset({"session/read"})

screen_contains = {
    "rust": (
        # The banner stays mounted while a child view is open.
        "Mistral Vibe",
        "Type /help",
        "No transcript yet.",
        "Viewing Explore 1 · read-only · Esc to return",
    )
}


def _taskless_spawn(task: str) -> AppServerEvent:
    spawn = subagent(task)
    spawn["params"]["entry"]["detail"]["input"] = {"agent": "explore"}
    return spawn


timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    _taskless_spawn(_TASK),
    child_session_updated(),
    turn_completed(),
    "\x1b[B",  # Down — focus the list on the Main row
    "\x1b[B",  # Down — highlight the child row
    "\r",  # Enter — open the read-only view of the empty child
]
