"""Tab-indented content in a written file and in an expanded shell transcript."""

from __future__ import annotations

from e2e.app_server.events import (
    bash,
    turn_completed,
    turn_started,
    user_msg,
    write_file,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "write the makefile"

# The settled bash disclosure header lands on SGR row 32.
_CLICK = "\x1b[<0;1;32M\x1b[<0;1;32m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    write_file("Makefile", "run:\n\tcargo run\n\techo done\n"),
    bash("make -n run", "cargo run\n\techo done\n\tdone\n"),
    turn_completed(),
    _CLICK,
]
