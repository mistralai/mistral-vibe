"""A model picked from a footer click switches the footer and leaves the draft in the composer."""

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

# The footer is row 40; "mistral-medium-3.5[medium]" fills columns 74-99 of 120.
_CLICK = "\x1b[<0;85;40M\x1b[<0;85;40m"

request_methods = {"config/reload"}
timeline: Timeline = ["draft text", _CLICK, "\x1b[B", "\x1b[B", "\r", "\r"]
screen_contains = ("draft text", "devstral-small[off] · 0/600k tokens (0%)")
screen_excludes = ("Select Model",)
