"""In a capped, scrolled chat input, the terminal cursor follows the caret on the last visible row."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SHIFT_ENTER = "\x1b[13;2u"

timeline: Timeline = [_SHIFT_ENTER.join(f"line {n}" for n in range(30))]

# The caret ends `line 29`: two-space gutter plus seven cells.
screen_cursor = {"rust": (37, 9)}
