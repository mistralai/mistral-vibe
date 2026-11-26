from __future__ import annotations

from e2e.app_server.scenario import Timeline, take_snapshot

# From medium: Up to off, wrap to high, then Down wraps back to medium.
timeline: Timeline = [
    "/thinking",
    take_snapshot(),
    "\r",
    take_snapshot(),
    "\x1b[A",
    take_snapshot(),
    "\x1b[A",
    take_snapshot(),
    "\x1b[A",
    take_snapshot(),
    "\x1b[B",
    take_snapshot(),
]
