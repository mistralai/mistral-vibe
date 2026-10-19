"""The account's plan offer is appended to the what's-new body as a link."""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import tempfile

from e2e.app_server.config import MINIMAL_HOME_CONFIG, WHATS_NEW_FIXTURE
from e2e.app_server.scenario import Action, Timeline

# A stale seen-version in a pinned VIBE_HOME trips the what's-new gate.
_home = tempfile.mkdtemp(prefix="e2e_whatsnew_offer_")
atexit.register(shutil.rmtree, _home, True)
Path(_home, "config.toml").write_text(MINIMAL_HOME_CONFIG)
Path(_home, "cache.toml").write_text(
    '[update_cache]\nlatest_version = "0.0.0"\nstored_at_timestamp = 0\n'
    'seen_whats_new_version = "0.0.0"\n'
)

env = {"VIBE_HOME": _home, "VIBE_WHATS_NEW_FILE": os.fspath(WHATS_NEW_FIXTURE)}

_URL = "https://chat.mistral.ai/code/extensions?focus=key"

handshake = {
    "account/read": {
        "account": {
            "status": "ready",
            "plan": {"kind": "api", "name": "FREE", "title": None},
            "planOffer": {"kind": "switch_api_key", "url": _URL},
            "rateLimitAction": None,
            "teleportEligible": True,
            "teleportAction": None,
        }
    }
}

screen_contains = ("Switch to your Vibe Pro API key",)
expected_actions = [Action("open_url", _URL)]

# The offer's `Vibe Pro API key` link spans one-based columns 18-33 on row 12.
_CLICK = "\x1b[<0;20;12M\x1b[<0;20;12m"

# The banner mounts after startup; the capture after typing settles it.
capture_startup = False
timeline: Timeline = ["x", _CLICK]
