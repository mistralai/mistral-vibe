"""An image path dropped while the terminal is unfocused still lands in the composer."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

capture_startup = False
screen_contains = ("[Image #1]",)

# Drag-and-drop from another app: focus out, bracketed paste, focus in.
timeline: Timeline = [
    "\x1b[O" + paste("/tmp/vibe_e2e_fixtures/dropped shot.png") + "\x1b[I"
]
