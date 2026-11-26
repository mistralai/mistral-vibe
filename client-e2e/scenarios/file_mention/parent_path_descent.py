"""Tab on an `@../` directory lists it first, then its entries; Down and Enter pick one."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

expected_clipboard = "@../whats_new_link/9.9.9.md ready"

timeline: Timeline = ["@../whats_new_l", "\t", "\x1b[B", "\r", "ready\x1b[18~\x19"]
