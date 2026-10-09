from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    read_file,
    subagent,
    subagent_notification,
    subagent_wait_completed,
    subagent_wait_started,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "delegate the investigation"
_TASK = "find the transcript grouping code"
_RESULT = "Found the grouping code in transcript/grouping.rs."
_CHILD_SESSION_ID = "child-session"


def child_user_msg(text: str) -> dict:
    event = user_msg(text)
    event["params"]["sessionId"] = _CHILD_SESSION_ID
    event["params"]["entry"]["sessionId"] = _CHILD_SESSION_ID
    return event


screen_contains = {"rust": ("Read 3 files, ran 1 subagent, called 1 tool", _RESULT)}
screen_excludes = {
    "rust": (
        f"> {_TASK}",
        "> Runtime notification:",
        "Subagent explore-1 completed turn 1.",
    )
}
skip_terminal_parity = "The Python client rejects child-session history notifications."

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    read_file("before.txt", "   1→before", num_lines=1),
    subagent(_TASK),
    subagent_wait_started(),
    read_file("during-one.txt", "   1→one", num_lines=1),
    child_user_msg(_TASK),
    subagent_notification(_RESULT),
    subagent_wait_completed(_RESULT),
    read_file("during-two.txt", "   1→two", num_lines=1),
    assistant_msg("Done."),
    turn_completed(),
    "\x1b[<0;1;15M\x1b[<0;1;15m",
    "\x1b[<0;5;17M\x1b[<0;5;17m",
]
