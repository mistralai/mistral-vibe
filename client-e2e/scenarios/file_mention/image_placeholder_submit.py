"""A pasted image shows as `[Image #1]`, is prepared as its path, and reaches the model by name."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    file_image_attachment,
    paste,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PASTED = "/tmp/vibe-e2e-shot.png"
_SNAPSHOT = "/vibe-e2e-snapshots/shot.png"
_PROMPT = "[Image #1] what is this"

# The recorded prepare request carries the image's `@path` mention.
request_methods = ("workspace/prompt/prepare",)
handshake = {
    "workspace/prompt/prepare": {
        "prompt": {
            "displayText": f"@{_PASTED} what is this",
            "promptText": f"@{_PASTED} what is this",
            "images": [file_image_attachment(_PASTED, _SNAPSHOT)],
            "autoTitle": None,
            "mentions": {
                "count": 1,
                "contextTypes": {"image": 1},
                "fileExtensions": {},
            },
        }
    }
}

capture_startup = False
capture_steps = {0, 1}
screen_contains = {"rust": (_PROMPT, "attached image: [Image #1]", "A screenshot.")}

timeline: Timeline = [
    paste(_PASTED),
    "what is this\r",
    turn_started(),
    user_msg(_PROMPT, images=[file_image_attachment("[Image #1]", _SNAPSHOT)]),
    assistant_msg("A screenshot."),
    turn_completed(),
]
