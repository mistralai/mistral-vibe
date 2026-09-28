"""A mouse selection copies nothing, and shows no notice, when autocopy is off."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"SSH_TTY": "/dev/pts/0"}

handshake = {
    "config/read": {"config": {"autocopyToClipboard": False}},
    "runtime/read": {"runtime": {"config": {"autocopyToClipboard": False}}},
}

_ROW = 20
_COL_START = 12
_COL_END = 48

_SELECT = (
    f"\x1b[<0;{_COL_START};{_ROW}M\x1b[<32;{_COL_END};{_ROW}M\x1b[<0;{_COL_END};{_ROW}m"
)


def _wide_reply(lines: int) -> str:
    return "\n\n".join(
        f"The quick brown fox jumps over the lazy dog on line {i:02d}."
        for i in range(1, lines + 1)
    )


timeline: Timeline = [
    "tell me a story\r",
    turn_started(),
    user_msg("tell me a story"),
    assistant_msg(_wide_reply(30)),
    turn_completed(),
    _SELECT,
]
