"""On a continuation row, the terminal cursor counts the gutter, tab stops and wide cells."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

_LEFT = "\x1b[D"

timeline: Timeline = [paste("first\n\t宽x"), _LEFT]

# The caret sits on `x`: two-space gutter, a 4-cell tab, then the 2-cell `宽`.
screen_cursor = {"rust": (36, 8)}
