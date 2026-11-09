"""A multi-line tool header copies its lines without the hang indent under the message column."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}

_PROMPT = "run the loop"
_COMMAND = 'for f in *.py; do\n  echo "$f"\ndone'

# The settled bash disclosure header lands on SGR row 11; unfolded, it spans rows 11-13.
_EXPAND = "\x1b[<0;1;11M\x1b[<0;1;11m"
# Drag from the header's first row to the end of the result body.
_SELECT = "\x1b[<0;1;11M\x1b[<32;60;14M\x1b[<0;60;14m"
# The command's own indentation survives; the header's hang indent does not.
expected_clipboard = 'Ran for f in *.py; do\n  echo "$f"\ndone\na.py'

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash(_COMMAND, "a.py"),
    assistant_msg("Done."),
    turn_completed(),
    _EXPAND,
    _SELECT,
]
