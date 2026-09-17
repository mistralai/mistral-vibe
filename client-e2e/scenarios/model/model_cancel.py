from __future__ import annotations

from e2e.app_server.scenario import Timeline

_ESCAPE = "\x1b[27u"

timeline: Timeline = ["/model", "\r", _ESCAPE, "x"]
