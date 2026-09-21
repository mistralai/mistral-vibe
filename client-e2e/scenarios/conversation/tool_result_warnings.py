"""An expanded read result with warnings stacks its warning lines above the body."""

from __future__ import annotations

from e2e.app_server.events import read_file, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PROMPT = "read the big file"
_CONTENT = "   1→first line\n   2→second line"

# The settled read-file disclosure header lands on SGR row 32.
_CLICK = "\x1b[<0;1;32M\x1b[<0;1;32m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    read_file(
        "app.log",
        _CONTENT,
        num_lines=2,
        warnings=["File is 95% of the quota", "Cleanup recommended"],
    ),
    turn_completed(),
    _CLICK,
]
