"""A bottom app replacing the chat input parks the terminal cursor on the static bottom-left cell."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/thinking", "\r"]

# Dead-key and IME previews draw at the terminal cursor: off the input, it must not drift.
screen_cursor = {"rust": (39, 0)}
