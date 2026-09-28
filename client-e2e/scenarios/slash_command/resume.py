from __future__ import annotations

from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"

timeline: Timeline = ["/resume", "\r", "\x1b[B", _ESCAPE]

capture_startup = False
capture_steps = {1, 2, 3}
