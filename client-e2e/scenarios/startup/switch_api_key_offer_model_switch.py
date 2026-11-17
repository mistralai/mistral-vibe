"""The switch-key box follows the active model's account read: a model switch whose read has no offer drops it."""

from __future__ import annotations

from e2e.app_server.scenario import Timeline
from scenarios.model.model_border_click_pick import DEVSTRAL

_SWITCH = {
    "status": "ready",
    "plan": {"kind": "api", "name": "FREE", "title": "Free"},
    "planOffer": {
        "kind": "switch_api_key",
        "url": "https://chat.mistral.ai/code/extensions?focus=key",
    },
    "rateLimitAction": None,
}
_NO_OFFER = {
    "status": "unavailable",
    "plan": None,
    "planOffer": None,
    "rateLimitAction": None,
}

# The startup read carries the switch offer; the read the model switch asks for has none.
handshake = {
    "account/read": [{"account": _SWITCH}, {"account": _NO_OFFER}],
    "config/model/write": {
        "runtime": {"config": {"activeModel": DEVSTRAL, "activeModelPinned": True}}
    },
}

request_methods = {"account/read"}

# The input box's top border is row 35; "mistral-medium-3.5[medium]" fills columns 78-103 of 120.
_ROW = 35
_CLICK = f"\x1b[<0;90;{_ROW}M\x1b[<0;90;{_ROW}m"

timeline: Timeline = ["x", _CLICK, "\x1b[B", "\x1b[B", "\r", "\r"]
screen_contains = ("─ devstral-small[off] · accept edits ─",)
screen_excludes = ("Vibe Pro plan", "does not use it")
