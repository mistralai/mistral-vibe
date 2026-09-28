from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

# Force deterministic OSC 52 copying across hosts.
env = {"SSH_TTY": "/dev/pts/0"}

# Select a wide mid-transcript row shared by both clients.
_ROW = 20
_COL_START = 12
_COL_MID = 30
_COL_END = 48

_PRESS = f"\x1b[<0;{_COL_START};{_ROW}M"
_DRAG_MID = f"\x1b[<32;{_COL_MID};{_ROW}M"
_DRAG_END = f"\x1b[<32;{_COL_END};{_ROW}M"
_RELEASE = f"\x1b[<0;{_COL_END};{_ROW}m"

_SELECT = _PRESS + _DRAG_MID + _DRAG_END + _RELEASE
_WHEEL_UP = "\x1b[<64;60;20M"
_WHEEL_DOWN = "\x1b[<65;60;20M"

_EDGE_ROW = 24
_EDGE_PRESS = f"\x1b[<0;{_COL_START};{_EDGE_ROW}M"
_EDGE_DRAG = f"\x1b[<32;120;{_EDGE_ROW}M"
_EDGE_RELEASE = f"\x1b[<0;120;{_EDGE_ROW}m"

_AUTO_PRESS = f"\x1b[<0;{_COL_START};{_EDGE_ROW}M"
_AUTO_DRAG = "\x1b[<32;120;1M"
_AUTO_RELEASE = "\x1b[<0;120;1m"
_TOP_SELECT = (
    f"\x1b[<0;{_COL_START};21M"
    f"\x1b[<32;{_COL_MID};21M"
    f"\x1b[<32;{_COL_END};21M"
    f"\x1b[<0;{_COL_END};21m"
)


def _wide_reply(lines: int) -> str:
    """Build deterministic wide Markdown paragraphs."""
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
    _WHEEL_UP * 2,
    _EDGE_PRESS + _EDGE_DRAG + _EDGE_RELEASE,
    _AUTO_PRESS,
    _AUTO_DRAG,
    _AUTO_RELEASE,
    _WHEEL_UP * 40,
    _TOP_SELECT,
    _WHEEL_UP * 2,
    _WHEEL_DOWN * 40,
    _WHEEL_DOWN * 2,
]

capture_steps = {0, 1, 2, 3, 8, 9, 10, 11}
