from __future__ import annotations

from e2e.app_server.scenario import Timeline

# A committed, fixed directory keeps the popup rows independent of the repo tree.
# The selected row is reverse-video; under ansi themes a color swap renders nothing.
timeline: Timeline = ["@at_popup/", "\x1b[B", "\x1b[A"]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
