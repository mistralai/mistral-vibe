from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    read_file,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "show the selected file"
_CONTENT = "   1→first selected line\n   2→second selected line"

# The settled read-file disclosure header lands on SGR row 30.
_CLICK = "\x1b[<0;1;30M\x1b[<0;1;30m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    read_file("selection.txt", _CONTENT, num_lines=2),
    assistant_msg("Done."),
    turn_completed(),
    _CLICK,
]
