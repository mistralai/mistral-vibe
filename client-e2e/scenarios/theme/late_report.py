from __future__ import annotations

from e2e.app_server.scenario import Timeline

_REPORT = "\x1b]11;rgb:1e1e/1e1e/2e2e\x1b\\"
timeline: Timeline = [_REPORT + "x"]
