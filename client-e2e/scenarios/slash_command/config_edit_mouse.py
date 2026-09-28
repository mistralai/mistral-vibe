"""Click the model below a wrapped label without saving its neighbour."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

capture_startup = False
timeline: Timeline = ["/config\r", "\r", "\x1b[<0;25;18M\x1b[<0;25;18m"]
