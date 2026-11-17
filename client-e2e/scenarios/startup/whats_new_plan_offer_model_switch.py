"""The upgrade box under what's-new follows the active model's account read: a switch whose read has no offer drops it."""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import tempfile

from e2e.app_server.config import MINIMAL_HOME_CONFIG, WHATS_NEW_FIXTURE
from e2e.app_server.scenario import Timeline, take_snapshot
from scenarios.model.model_border_click_pick import DEVSTRAL

# A stale seen-version in a pinned VIBE_HOME trips the what's-new gate.
_home = tempfile.mkdtemp(prefix="e2e_whatsnew_offer_switch_")
atexit.register(shutil.rmtree, _home, True)
Path(_home, "config.toml").write_text(MINIMAL_HOME_CONFIG)
Path(_home, "cache.toml").write_text(
    '[update_cache]\nlatest_version = "0.0.0"\nstored_at_timestamp = 0\n'
    'seen_whats_new_version = "0.0.0"\n'
)

env = {"VIBE_HOME": _home, "VIBE_WHATS_NEW_DIR": os.fspath(WHATS_NEW_FIXTURE)}

_UPGRADE = {
    "status": "ready",
    "plan": {"kind": "chat", "name": "FREE", "title": "Free"},
    "planOffer": {
        "kind": "upgrade_to_pro",
        "url": "https://admin.mistral.ai/subscriptions/upgrade/plans",
    },
    "rateLimitAction": None,
}
_NO_OFFER = {
    "status": "unavailable",
    "plan": None,
    "planOffer": None,
    "rateLimitAction": None,
}

# The startup read carries the upgrade offer; the read the model switch asks for has none.
handshake = {
    "account/read": [{"account": _UPGRADE}, {"account": _NO_OFFER}],
    "config/model/write": {
        "runtime": {"config": {"activeModel": DEVSTRAL, "activeModelPinned": True}}
    },
}

request_methods = {"account/read"}

# The input box's top border is row 35; "mistral-medium-3.5[medium]" fills columns 78-103 of 120.
_ROW = 35
_CLICK = f"\x1b[<0;90;{_ROW}M\x1b[<0;90;{_ROW}m"

# The snapshot pins the box before the switch; the final frame must not show it.
timeline: Timeline = ["x", take_snapshot(), _CLICK, "\x1b[B", "\x1b[B", "\r", "\r"]
screen_contains = ("Release notes", "─ devstral-small[off] · accept edits ─")
screen_excludes = ("Vibe Pro plan", "Upgrade to Vibe Pro")
