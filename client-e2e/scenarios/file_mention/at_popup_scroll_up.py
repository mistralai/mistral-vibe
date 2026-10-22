"""Moving up past the widest file label keeps the selected row in view."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# Up wraps to the last entry, then climbs back above the bottom viewport.
timeline: Timeline = ["@client-e2e/fixtures/at_popup_scroll/", "\x1b[A", "\x1b[A" * 10]

# Each key must land on a settled UI: batching changes the outcome here.
settle_per_key = True
