"""Scenario: a drag from transcript text into the composer stays transcript-only.

The drag crosses the loading area and input box after selecting transcript text.
The composer remains outside the transcript selection, including during copy.

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


# Same reply shape as `text_selection`, proven to render identically on both
# frontends, so the only signal is the full-screen selection highlight.
def _wide_reply(lines: int) -> str:
    return "\n\n".join(
        f"The quick brown fox jumps over the lazy dog on line {i:02d}."
        for i in range(1, lines + 1)
    )


# Press mid chat area, drag within the transcript, then down into the input box.
# The transcript owns the selection for the entire gesture.
_PRESS = "\x1b[<0;4;18M"
_DRAG_MID = "\x1b[<32;60;28M"
_DRAG_END = "\x1b[<32;40;36M"
_RELEASE = "\x1b[<0;40;36m"

_SELECT = _PRESS + _DRAG_MID + _DRAG_END + _RELEASE
_SCROLL_UP = "\x1b[1;2A"

timeline: Timeline = [
    "tell me a story\r",
    turn_started(),
    user_msg("tell me a story"),
    assistant_msg(_wide_reply(30)),
    turn_completed(),
    "a draft in the composer",
    _SELECT,
    _SCROLL_UP * 6,
]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
