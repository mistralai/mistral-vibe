"""The Mistral Large cat keeps the info block on its three rows, and a copy skips the cat."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline

_SHIFT_TAB = "\x1b[Z"
# Drag from the cat on the version row (SGR 2) to the end of the `Type /help` row (SGR 4).
_SELECT = "\x1b[<0;1;2M\x1b[<32;60;4M\x1b[<0;60;4m"

env = {"SSH_TTY": "/dev/pts/0"}
handshake = {
    "runtime/read": {
        "runtime": {"config": {"activeModel": {"displayName": "mistral-large-3"}}}
    }
}
screen_contains = ("mistral-large-3[medium]",)
expected_clipboard = (
    "Mistral Vibe v0.0.0-eval\nmistral-large-3[medium]\nType /help for more information"
)

# Settle a banner frame with Shift+Tab, then copy the whole banner.
timeline: Timeline = [_SHIFT_TAB, _SELECT]
