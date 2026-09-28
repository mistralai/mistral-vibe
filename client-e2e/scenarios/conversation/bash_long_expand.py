from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "count the python lines"

# Wider than the 120-column header: ellipsis when collapsed, hanging wrap when expanded.
_COMMAND = (
    "find . -type f -name '*.py' -not -path './.venv/*' "
    "| xargs wc -l | sort -rn | head -20 | tee /tmp/python-line-counts.txt"
)

# The settled bash disclosure header lands on SGR row 30.
_CLICK = "\x1b[<0;1;30M\x1b[<0;1;30m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash(_COMMAND, "  4212 total"),
    assistant_msg("Done."),
    turn_completed(),
    _CLICK,
]
