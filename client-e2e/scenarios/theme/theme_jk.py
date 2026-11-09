"""j and k move the /theme highlight like the arrow keys."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

capture_startup = False
timeline: Timeline = ["/theme", "\r", "j", "j", "k"]
