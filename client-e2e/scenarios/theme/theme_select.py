from __future__ import annotations

from e2e.app_server.scenario import Timeline

# A pick only writes /theme; a stray config/reload would show up here.
request_methods = {"config/reload"}
timeline: Timeline = ["/theme", "\r", "\x1b[B", "\r"]
