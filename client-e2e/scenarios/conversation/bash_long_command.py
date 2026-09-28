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

# Wider than the 120-column header, so the collapsed summary must ellipsis.
_COMMAND = (
    "find . -type f -name '*.py' -not -path './.venv/*' "
    "| xargs wc -l | sort -rn | head -20 | tee /tmp/python-line-counts.txt"
)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash(_COMMAND, "  4212 total"),
    assistant_msg("Done."),
    turn_completed(),
]
