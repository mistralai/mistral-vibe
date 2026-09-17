from __future__ import annotations

from e2e.app_server.scenario import Timeline

# The selected row is reverse-video; under ansi themes a color swap renders nothing.
timeline: Timeline = ["@cli", "\x1b[B", "\x1b[A"]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
