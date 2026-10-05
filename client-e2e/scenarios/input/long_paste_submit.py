"""A collapsed long paste is atomic, submitted in full, and stays collapsed once sent.

The paste ends with a newline, as editor copies do, and closes the message, so the
submit trim cuts it: the sent message must still show the composer's placeholder.
"""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    paste,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_TEXT = "\n".join(f"row {number}" for number in range(12))
_PASTE = f"{_TEXT}\n"
_BACKSPACE = "\x7f"
_PROMPT = "summarize "
_SUBMITTED = f"{_PROMPT}{_TEXT}"


def _fingerprint(text: str) -> str:
    """FNV-1a 64, as the client marks a collapsed paste."""
    value = 0xCBF29CE484222325
    for byte in text.encode():
        value = ((value ^ byte) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"{value:016x}"


# The display content the client annotates the entry with, as the server echoes it.
_DISPLAY = {
    "version": "1",
    "host": "vibe",
    "content": [
        {
            "type": "vibe.collapsed_paste",
            "start": len(_PROMPT),
            "chars": len(_TEXT),
            "hash": _fingerprint(_TEXT),
            "pasted": len(_PASTE),
        }
    ],
}
_ECHO = user_msg(_SUBMITTED)
_ECHO["params"]["entry"]["userDisplayContent"] = _DISPLAY

# The recorded prepare request carries the expanded text, not the placeholder.
request_methods = ("workspace/prompt/prepare",)
handshake = {
    "workspace/prompt/prepare": {
        "prompt": {
            "displayText": _SUBMITTED,
            "promptText": _SUBMITTED,
            "images": [],
            "autoTitle": None,
            "mentions": {"count": 0, "contextTypes": {}, "fileExtensions": {}},
        }
    }
}

capture_startup = False
capture_steps = {1, 2, 3}
screen_contains = {"rust": ("> summarize [Pasted 74 characters]", "Twelve rows.")}
screen_excludes = {"rust": ("row 5",)}

# One Backspace after the placeholder removes it whole; the second paste then submits.
timeline: Timeline = [
    paste(_PASTE),
    _BACKSPACE,
    _PROMPT + paste(_PASTE),
    "\r",
    turn_started(),
    _ECHO,
    assistant_msg("Twelve rows."),
    turn_completed(),
]
