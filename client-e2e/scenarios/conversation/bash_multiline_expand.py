from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "run the loop"

# Collapsed flattens the newlines away; expanded restores them under the message column.
_COMMAND = 'for f in *.py; do\n  echo "$f"\ndone'

# The settled bash disclosure header lands on SGR row 30.
_CLICK = "\x1b[<0;1;30M\x1b[<0;1;30m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash(_COMMAND, "a.py"),
    assistant_msg("Done."),
    turn_completed(),
    _CLICK,
]
