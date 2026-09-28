"""Editing a queued image prompt preserves its attachment."""

from __future__ import annotations

from e2e.app_server.events import assistant_msg, turn_started, user_msg
from e2e.app_server.scenario import Timeline

env = {"VIBE_REPLAY_SETTLE_BUSY": "1"}

_PROMPT = "@/vibe-test/diagram.png describe this image"
_IMAGE = {
    "source": {"kind": "file", "path": "/vibe-test/diagram.png"},
    "alias": "diagram.png",
    "mimeType": "image/png",
}
_UP = "\x1b[A"

handshake = {
    "workspace/prompt/prepare": {
        "prompt": {
            "displayText": _PROMPT,
            "promptText": _PROMPT,
            "images": [_IMAGE],
            "autoTitle": _PROMPT,
            "mentions": {
                "count": 1,
                "contextTypes": {"image": 1},
                "fileExtensions": {".png": 1},
            },
        }
    }
}
settle_per_key = True
screen_contains = {
    "python": ("describe this image edited", "attached image: diagram.png"),
    "rust": ("describe this image edited", "attached image: diagram.png"),
}
screen_excludes = {
    "python": ("queue/replace failed",),
    "rust": ("queue/replace failed",),
}

timeline: Timeline = [
    "first\r",
    turn_started(),
    user_msg("first", images=[_IMAGE]),
    assistant_msg("Working on it."),
    {"release": 4},
    f"{_PROMPT}\r",
    _UP,
    "\r",
    " edited\r",
]
