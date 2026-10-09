"""The `/config` modal hides the chat caret, so the terminal cursor parks bottom-left."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

timeline: Timeline = ["/config\r"]

# Dead-key and IME previews draw at the terminal cursor: never under the modal's rows.
screen_cursor = {"rust": (39, 0)}
