from __future__ import annotations

from e2e.app_server.scenario import Timeline

# From medium: Up to off, wrap to high, then Down wraps back to medium.
timeline: Timeline = ["/thinking", "\r", "\x1b[A", "\x1b[A", "\x1b[A", "\x1b[B"]
