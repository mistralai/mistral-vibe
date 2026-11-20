"""Tool output opening with the border glyphs `│` and `└` keeps them on copy; only the border is chrome."""

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

_PROMPT = "show the tree"
_STDOUT = "src\n│ main.rs\n└ lib.rs"

# The settled bash disclosure header lands on SGR row 11; unfolded, the body spans rows 12-14.
_EXPAND = "\x1b[<0;1;11M\x1b[<0;1;11m"
_SELECT = "\x1b[<0;1;12M\x1b[<32;60;14M\x1b[<0;60;14m"
expected_clipboard = _STDOUT

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash("tree", _STDOUT),
    assistant_msg("Done."),
    turn_completed(),
    _EXPAND,
    _SELECT,
]
