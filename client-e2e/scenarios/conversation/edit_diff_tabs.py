"""An edit diff whose lines are tab-indented, as in a Makefile."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    edit_file,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "edit the makefile"

_OLD = "run:\n\tcargo run\n\techo done\n"
_NEW = "run:\n\tcargo run -- $(ARGS)\n\techo done\n"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    edit_file("Makefile", [(16, _OLD, _NEW)]),
    assistant_msg("Done."),
    turn_completed(),
]
