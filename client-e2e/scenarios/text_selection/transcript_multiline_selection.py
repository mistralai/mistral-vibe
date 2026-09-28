"""Scenario: mouse drag-select several wrapped lines of one assistant paragraph.

Unlike `text_selection` (a single mid-chat row), this drags top-left to
bottom-right across several rows of a single soft-wrapped paragraph. The reply is
one long paragraph (no blank separators), so it wraps into contiguous full-width
rows -- the intermediate rows are entirely text, so the flow selection highlights
whole rows with no trailing-whitespace ambiguity, and the start/end rows clip to
the drag columns. Identical wrapping on both frontends (see markdown_render) makes
the multi-row highlight land on the same cells.

SGR mouse reports (1-based col;row), left button = 0:
- `\\x1b[<0;C;RM`   left-button press at (C, R)
- `\\x1b[<32;C;RM`  motion with left held (button 0 + 32 = drag)
- `\\x1b[<0;C;Rm`   left-button release (lowercase `m`)
"""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

# Force the OSC 52 copy path so the copy notice is deterministic across hosts.
env = {"SSH_TTY": "/dev/pts/0"}


# Wide, deterministic paragraphs (blank-line separated), the same reply shape as
# `text_selection` -- proven to render identically on both frontends -- so the
# only signal here is the multi-row selection highlight.
def _wide_reply(lines: int) -> str:
    return "\n\n".join(
        f"The quick brown fox jumps over the lazy dog on line {i:02d}."
        for i in range(1, lines + 1)
    )


# Drag across several mid-chat rows, releasing past end-of-line so the end row
# clamps to its content (as Textual does) rather than to the release cell.
_PRESS = "\x1b[<0;4;18M"
_DRAG_MID = "\x1b[<32;40;21M"
_DRAG_END = "\x1b[<32;80;24M"
_RELEASE = "\x1b[<0;80;24m"

_SELECT = _PRESS + _DRAG_MID + _DRAG_END + _RELEASE

timeline: Timeline = [
    "tell me a story\r",
    turn_started(),
    user_msg("tell me a story"),
    assistant_msg(_wide_reply(30)),
    turn_completed(),
    _SELECT,
]
