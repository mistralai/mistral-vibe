"""A key that bypasses the user's Vibe plan gets a switch offer under the banner on every start."""

from __future__ import annotations

from e2e.app_server.scenario import Action, Timeline, take_snapshot

_URL = "https://chat.mistral.ai/code/extensions?focus=key"

handshake = {
    "account/read": {
        "account": {
            "status": "ready",
            "plan": {"kind": "api", "name": "FREE", "title": "Free"},
            "planOffer": {"kind": "switch_api_key", "url": _URL},
            "rateLimitAction": None,
        }
    }
}

screen_contains = (
    "· Free",
    "You have Vibe Pro, but this API key does not use it.",
    "quit and run vibe --setup, then choose Launch browser and sign in",
)
expected_actions = [Action("open_url", _URL)]

# The offer's `Vibe Pro API key` link starts at one-based column 101 on row 9.
_CLICK = "\x1b[<0;104;9M\x1b[<0;104;9m"

# The offer mounts after startup; typing settles it before the snapshot.
timeline: Timeline = ["x", take_snapshot(), _CLICK]
