"""Directory completion permits typing a child path and accepting it with Enter."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

expected_clipboard = "@data/fixture.json ready"

timeline: Timeline = ["@data", "\t", "fixture.j", "\r", "ready\x1b[18~\x19"]
