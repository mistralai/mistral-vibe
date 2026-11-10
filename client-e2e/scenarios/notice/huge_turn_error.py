"""A turn failing with a megabyte-long error mounts a capped error row (VIBE-5615)."""

from __future__ import annotations

from e2e.app_server.events import turn_failed, turn_started, user_msg
from e2e.app_server.scenario import Timeline

_PREFIX = "Image is an animated GIF. Image URL: data:image/gif;base64,"

timeline: Timeline = [
    "show me\r",
    turn_started(),
    user_msg("show me"),
    turn_failed(code="backend_error", message=_PREFIX + "R0lGODlh" * 150_000),
]

screen_contains = ("[truncated]", "/retry")
