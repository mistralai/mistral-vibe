"""A pasted image whose preparation crashes shows the error instead of sending the raw text and its unsnapshotted file."""

from __future__ import annotations

from e2e.app_server.events import paste
from e2e.app_server.scenario import Timeline

_MESSAGE = "prompt preparation crashed"
handshake = {
    "workspace/prompt/prepare": {
        "error": {"code": "internal_error", "message": _MESSAGE}
    }
}
request_methods = ("workspace/prompt/prepare",)
screen_contains = (_MESSAGE,)
timeline: Timeline = [paste("/Users/me/Desktop/shot one.png"), "what is this\r"]
