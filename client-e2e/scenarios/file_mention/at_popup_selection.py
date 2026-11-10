from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

# A committed, fixed directory keeps the popup rows independent of the repo tree.
# The selected row is reverse-video; under ansi themes a color swap renders nothing.
timeline: Timeline = ["@at_popup/", "\x1b[B", "\x1b[A", take_snapshot()]
