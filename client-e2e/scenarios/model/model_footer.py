"""The footer names the active model before the token counter and follows a /model pick."""

from __future__ import annotations

from typing import Any

from e2e.app_server.scenario import Timeline

DEVSTRAL: dict[str, Any] = {
    "name": "devstral-small-latest",
    "alias": "devstral-small",
    "thinking": "off",
    "supportsImages": False,
    "displayName": "devstral-small",
}

handshake = {
    "config/reload": {
        "runtime": {"config": {"activeModel": DEVSTRAL, "activeModelPinned": True}}
    }
}

request_methods = {"config/reload"}
# The first frame shows the startup model; pick devstral-small and its current level.
timeline: Timeline = ["/model", "\r", "\x1b[B", "\x1b[B", "\r", "\r"]
screen_contains = ("devstral-small[off] · 0/600k tokens (0%)",)
screen_excludes = ("mistral-medium-3.5[medium] · ",)
