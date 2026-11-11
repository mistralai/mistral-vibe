"""Without pay-as-you-go a nearly spent allowance fills the usage gauge in the error color, with no other label."""

from __future__ import annotations

import copy

from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.footer.usage_gauge import handshake as _base

handshake = copy.deepcopy(_base)
handshake["account/read"]["account"]["budget"] = {
    "usagePercentage": 100.0,
    "paygEnabled": False,
}

timeline: Timeline = [take_snapshot()]
screen_contains = ("usage ██████ 100% · ctx",)
screen_excludes = ("paused", "PAYG")
