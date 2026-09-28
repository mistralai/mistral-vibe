"""Scenario: select a transcript line, then scroll -- the highlight follows it.

A text selection is anchored to the transcript content, not to fixed screen
cells: once selected, scrolling the chat must move the highlight with the line it
covers (Textual re-projects the selection on scroll; the Rust TUI matches).

Scrolling is driven with Shift+Up/Down, not the mouse wheel: keyboard scroll is
instant on both frontends (`scroll_relative(animate=False)`) and steps by the same
5 rows, so the scroll state is deterministic frame-to-frame. The mouse wheel
animates in Textual, which leaves the scrollbar thumb settling sub-row and makes
the capture flaky. The reply is the wide, blank-separated shape from
`text_selection`, proven to render identically, so the only signal is where the
highlight lands as the view scrolls up and back.

Bytes: Shift+Up = `\\x1b[1;2A`, Shift+Down = `\\x1b[1;2B`. SGR mouse (1-based
col;row), left=0: `\\x1b[<0;C;RM` press, `\\x1b[<32;C;RM` drag, `\\x1b[<0;C;Rm`
release.
"""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

# Force the OSC 52 copy path so the copy notice is deterministic across hosts.
env = {"SSH_TTY": "/dev/pts/0"}


def _wide_reply(lines: int) -> str:
    return "\n\n".join(
        f"The quick brown fox jumps over the lazy dog on line {i:02d}."
        for i in range(1, lines + 1)
    )


_SHIFT_UP = "\x1b[1;2A"
_SHIFT_DOWN = "\x1b[1;2B"

# Select a text line high in the viewport (0-based row 9); each Shift+Up scrolls
# the chat up 5 rows, moving the content -- and the highlight -- down by 5.
_ROW = 10
_PRESS = f"\x1b[<0;12;{_ROW}M"
_DRAG_MID = f"\x1b[<32;30;{_ROW}M"
_DRAG_END = f"\x1b[<32;48;{_ROW}M"
_RELEASE = f"\x1b[<0;48;{_ROW}m"
_SELECT = _PRESS + _DRAG_MID + _DRAG_END + _RELEASE

timeline: Timeline = [
    "tell me a story\r",
    turn_started(),
    user_msg("tell me a story"),
    assistant_msg(_wide_reply(20)),
    turn_completed(),
    _SELECT,
    _SHIFT_UP * 3,
    _SHIFT_DOWN * 3,
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
