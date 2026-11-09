"""Clicking a blank composer row maps to the nearest document line safely."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# The three-row composer has only one document line. A click on its second visual
# row must use that line's horizontal offset, not manufacture an out-of-bounds
# byte position past its UTF-8 text.
_BLANK_ROW_CLICK = "\x1b[<0;8;37M\x1b[<0;8;37m"

timeline: Timeline = ["café naïve", _BLANK_ROW_CLICK]
