"""With a selection showing, a press on a header's hang indent still anchors a new selection."""

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
clipboard_clients = {"rust"}

_PROMPT = "run the loop"
_COMMAND = 'for f in *.py; do\n  echo "$f"\ndone'

# The settled bash disclosure header lands on SGR row 15; unfolded, it spans rows 15-17.
_EXPAND = "\x1b[<0;1;15M\x1b[<0;1;15m"
# A first selection on the "Done." reply stays showing after its copy.
_SELECT_REPLY = "\x1b[<0;3;20M\x1b[<32;8;20M\x1b[<0;8;20m"
# Press inside the hang indent of the header's second row, then drag to the body's end.
_SELECT_FROM_HANG = "\x1b[<0;3;16M\x1b[<32;60;18M\x1b[<0;60;18m"
expected_clipboard = '  echo "$f"\ndone\na.py'

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash(_COMMAND, "a.py"),
    assistant_msg("Done."),
    turn_completed(),
    _EXPAND,
    _SELECT_REPLY,
    _SELECT_FROM_HANG,
]
