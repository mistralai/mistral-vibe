"""Edge auto-scroll copies the complete document range, including off-screen rows."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}
clipboard_clients = {"rust"}
clipboard_contains = ("line 01.", "line 25.")
clipboard_excludes = ("line 27.",)


def _wide_reply(lines: int) -> str:
    return "\n\n".join(
        f"The quick brown fox jumps over the lazy dog on line {index:02d}."
        for index in range(1, lines + 1)
    )


timeline: Timeline = [
    "select across scrolling\r",
    turn_started(),
    user_msg("select across scrolling"),
    assistant_msg(_wide_reply(30)),
    turn_completed(),
    "\x1b[<0;12;24M",
    "\x1b[<32;120;1M",
    "\x1b[<0;120;15m",
]
