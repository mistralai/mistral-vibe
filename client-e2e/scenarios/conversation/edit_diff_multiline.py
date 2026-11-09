"""A diff inside a docstring highlights the edited rows as string, not code."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    edit_file,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"VIBE_THEME": "textual-dark"}
handshake = {
    "config/read": {"config": {"theme": "textual-dark"}},
    "runtime/read": {"runtime": {"config": {"theme": "textual-dark"}}},
}
_PROMPT = "fix the docstring"
_OLD = 'def run():\n    """Run the job.\n\n    if the queue is empty, return early\n    """\n    return 1\n'
_NEW = 'def run():\n    """Run the job.\n\n    if the queue is empty, return None\n    """\n    return 1\n'
# The lone edit stays ungrouped, so its own header row unfolds the diff.
_EXPAND_EDIT = "\x1b[<0;1;11M\x1b[<0;1;11m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    edit_file("job.py", [(10, _OLD, _NEW)]),
    assistant_msg("Done."),
    turn_completed(),
    _EXPAND_EDIT,
]
