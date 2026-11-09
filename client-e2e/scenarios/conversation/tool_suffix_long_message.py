"""A header's suffix ends it: after the ellipsis when collapsed, after the last word when expanded."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "start the dev servers"

# Wider than the 120-column header, so the collapsed message must ellipsize before the suffix.
_COMMAND = (
    "uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload "
    "--reload-dir app --log-level debug --proxy-headers --forwarded-allow-ips '*'"
)

# Its last expanded row is too full for the suffix, which then opens its own row.
_FULL_ROW_COMMAND = 'sleep 300 && echo "server ready" ' + " ".join(
    f"argument-{index:02d}" for index in range(1, 16)
)

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash(_COMMAND, suffix="(running)"),
    bash(_FULL_ROW_COMMAND, suffix="(running)"),
    assistant_msg("Done."),
    turn_completed(),
    # Expand the group header, then the first call's header, then the second call's.
    "\x1b[<0;1;11M\x1b[<0;1;11m",
    "\x1b[<0;5;12M\x1b[<0;5;12m",
    "\x1b[<0;5;15M\x1b[<0;5;15m",
]
