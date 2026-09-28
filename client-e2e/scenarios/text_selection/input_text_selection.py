from __future__ import annotations

from e2e.app_server.scenario import Timeline

# Force deterministic OSC 52 copying across hosts.
env = {"SSH_TTY": "/dev/pts/0"}

_TEXT = "the quick brown fox jumps over the lazy dog"

# SGR row 36 is the first input row and text starts at column 3.
_ROW = 36
_COL_START = 7
_COL_MID = 20
_COL_END = 33

_PRESS = f"\x1b[<0;{_COL_START};{_ROW}M"
_DRAG_MID = f"\x1b[<32;{_COL_MID};{_ROW}M"
_DRAG_END = f"\x1b[<32;{_COL_END};{_ROW}M"
_RELEASE = f"\x1b[<0;{_COL_END};{_ROW}m"

_SELECT = _PRESS + _DRAG_MID + _DRAG_END + _RELEASE

timeline: Timeline = [_TEXT, _SELECT]
