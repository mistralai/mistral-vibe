"""A refused Shift+Tab switch shows the server's reason and the next press steps past it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SHIFT_TAB = "\x1b[Z"
# One canned answer serves every switch, so the reason names no agent.
_REASON = (
    "tools: tools.bash: default_timeout is set without max_timeout_seconds. "
    "Set both, or neither."
)

handshake = {
    "session/agent/update": {"error": {"code": "invalid_params", "message": _REASON}}
}

# The server refuses every switch: the first press asks for smart approve, the
# second steps past it to auto approve (the request golden); the label stays.
timeline: Timeline = [_SHIFT_TAB, _SHIFT_TAB]

screen_contains = (
    "Failed to switch agent: tools: tools.bash",
    "neither.",
    "accept edits",
)
