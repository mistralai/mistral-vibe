"""A tool-call display suffix renders inline after the settled header message."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "read the big log"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash("tail app.log", stdout="last line", suffix="(truncated)"),
    bash("tail other.log", stdout="last line", suffix="truncated"),
    assistant_msg("Done."),
    turn_completed(),
]
