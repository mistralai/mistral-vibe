"""A bare /rename shows the full usage tip, including its <title> placeholder."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

# No session/rename request is ever sent: the usage guard fires first.
timeline: Timeline = ["/rename\r"]
screen_contains = {"rust": ("Error: Usage: /rename <title>",)}
screen_excludes = {"rust": ("Usage: /rename\r",)}
