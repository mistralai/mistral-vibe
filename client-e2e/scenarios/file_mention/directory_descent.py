"""Directory completion permits typing a child path and accepting it with Enter."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

capture_startup = False
capture_steps = {1, 2, 4}
settle_per_key = True
expected_clipboard = "@client-e2e/fixtures/fixture.json ready"

timeline: Timeline = [
    "@client-e2e/fixtures",
    "\t",
    "fixture.j",
    "\r",
    "ready\x1b[18~\x19",
]
