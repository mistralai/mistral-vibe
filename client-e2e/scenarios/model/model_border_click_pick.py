"""A model picked by clicking the input box's model renames it there and leaves the draft in the composer."""

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
    "config/model/write": {
        "runtime": {"config": {"activeModel": DEVSTRAL, "activeModelPinned": True}}
    }
}

# The input box's top border is row 35; "mistral-medium-3.5[medium]" fills columns 78-103 of 120.
_CLICK = "\x1b[<0;85;35M\x1b[<0;85;35m"

request_methods = {"config/reload"}
timeline: Timeline = ["draft text", _CLICK, "\x1b[B", "\x1b[B", "\r", "\r"]
screen_contains = ("draft text", "─ devstral-small[off] · accept edits ─")
screen_excludes = ("Select Model",)
