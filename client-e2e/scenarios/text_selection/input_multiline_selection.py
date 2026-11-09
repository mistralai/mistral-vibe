"""Scenario: type a multi-line draft, then mouse drag-select across two rows.

Extends `input_text_selection` (single input row) to a multi-line draft: three
lines entered with Ctrl+J newlines, then a drag from the first content row into
the second. The input box grows to fit and stays bottom-anchored, so the rows are
deterministic. Each keystroke is its own PTY read (see input_newline) so a newline
insert does not reorder against following characters.

SGR mouse reports (1-based col;row), left button = 0:
- `\\x1b[<0;C;RM`   left-button press at (C, R)
- `\\x1b[<32;C;RM`  motion with left held (button 0 + 32 = drag)
- `\\x1b[<0;C;Rm`   left-button release (lowercase `m`)
"""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# Force the OSC 52 copy path so the copy notice is deterministic across hosts.
env = {"SSH_TTY": "/dev/pts/0"}

# Three content lines -> input box = 3 rows + 2 borders, bottom-anchored above the
# 1-row bottom bar: content rows are 0-based 35-37 (SGR 36-38). Drag from line one
# into line two, releasing past end-of-line so the end row clamps to its content.
_PRESS = "\x1b[<0;5;36M"
_DRAG_MID = "\x1b[<32;14;36M"
_DRAG_END = "\x1b[<32;40;37M"
_RELEASE = "\x1b[<0;40;37m"

_SELECT = _PRESS + _DRAG_MID + _DRAG_END + _RELEASE

timeline: Timeline = [
    "the quick brown",
    "\n",  # Ctrl+J newline
    "fox jumps over",
    "\n",
    "the lazy dog",
    _SELECT,
]
