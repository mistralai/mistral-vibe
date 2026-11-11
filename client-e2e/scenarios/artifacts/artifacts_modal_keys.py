"""Home, End, PageUp and PageDown move the highlight and clamp; Ctrl+C closes."""

from __future__ import annotations

from e2e.app_server.config import ROWS
from e2e.app_server.events import artifact
from e2e.app_server.scenario import Timeline

_PRS = [
    artifact(
        f"artifact-pr-{number}",
        number=number,
        url=f"https://github.com/mistral-ai/vibe/pull/{number}",
        discovered_at=f"2026-08-27T{number - 90:02d}:00:00Z",
        display_title=f"Change {number}",
    )
    for number in range(100, 130)
]

handshake = {"artifacts/list": {"artifacts": _PRS}}
request_methods = {"artifacts/list"}

# Ctrl+C closed the modal without arming the quit confirmation.
screen_contains = ("PRs (30)",)
screen_excludes = ("Pull Requests", "again to quit")

_LABEL = "PRs (30)"
_CLICK_COL = len("/test/workdir  ") + len(_LABEL) // 2 + 1
_BAR_CLICK = f"\x1b[<0;{_CLICK_COL};{ROWS}M\x1b[<0;{_CLICK_COL};{ROWS}m"

timeline: Timeline = [
    _BAR_CLICK,
    "\x1b[F",  # End: the oldest PR
    "\x1b[6~",  # PageDown stays on the last row
    "\x1b[5~",  # PageUp: one page up
    "\x1b[H",  # Home: the latest PR
    "\x1b[5~",  # PageUp stays on the first row
    "\x1b[6~",  # PageDown: one page down
    "\x03",  # Ctrl+C closes the modal
]
