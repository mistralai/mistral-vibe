"""Picking another model re-reads the account, so the usage gauge comes back for the new model."""

from __future__ import annotations

import copy

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.footer.usage_gauge import handshake as _usage
from scenarios.model.model_border_click_pick import DEVSTRAL

handshake = copy.deepcopy(_usage)
handshake["config/model/write"] = {
    "runtime": {"config": {"activeModel": DEVSTRAL, "activeModelPinned": True}}
}

# The startup read, then the one the switch to devstral-small asks for.
request_methods = {"account/read"}

# The input box's top border is row 35; "devstral-small[off]" fills columns 85-103 of 120.
_ROW = 35
_CLICK = f"\x1b[<0;90;{_ROW}M\x1b[<0;90;{_ROW}m"

timeline: Timeline = [
    _CLICK,
    take_snapshot(),
    "\x1b[B",
    take_snapshot(),
    "\x1b[B",
    take_snapshot(),
    "\r",
    take_snapshot(),
    "\r",
    take_snapshot(),
]
screen_contains = ("─ devstral-small[off] · accept edits ─", "usage ██▍    40% · ctx")
