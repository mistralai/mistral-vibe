from __future__ import annotations

from e2e.app_server.scenario import Timeline

# From medium: Up to low, off, wrap to max, then Down wraps back to off.
timeline: Timeline = ["/thinking", "\r", "\x1b[A", "\x1b[A", "\x1b[A", "\x1b[B"]
