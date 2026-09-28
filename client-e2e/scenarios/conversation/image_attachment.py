"""Prepared images reach the turn and render as clickable user attachments."""

from __future__ import annotations

from e2e.app_server.events import turn_completed, turn_started, user_msg
from e2e.app_server.scenario import Action, Timeline

_PROMPT = "inspect this image"
_PATH = "/vibe-test/diagram.png"
_IMAGE = {
    "source": {"kind": "file", "path": _PATH},
    "alias": "diagram.png",
    "mimeType": "image/png",
}
_PROJECTED_IMAGE = {
    "source": {"kind": "inline", "data": "aW1hZ2U="},
    "alias": "image",
    "mimeType": "image/png",
}

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

screen_contains = {
    "python": ("└ attached image: diagram.png",),
    "rust": ("└ attached image: diagram.png",),
}
expected_actions = {
    "python": [Action("open_url", f"file://{_PATH}")],
    "rust": [Action("open_url", f"file://{_PATH}")],
}
_CLICK_ATTACHMENT = "\x1b[<0;21;31M\x1b[<0;21;31m"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT, images=[_PROJECTED_IMAGE]),
    turn_completed(),
    _CLICK_ATTACHMENT,
]
