"""An upgrade offer shows in its own box under the what's-new body, as a link."""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import tempfile

from e2e.app_server.config import MINIMAL_HOME_CONFIG, WHATS_NEW_FIXTURE
from e2e.app_server.scenario import Action, Timeline, take_snapshot

# A stale seen-version in a pinned VIBE_HOME trips the what's-new gate.
_home = tempfile.mkdtemp(prefix="e2e_whatsnew_offer_")
atexit.register(shutil.rmtree, _home, True)
Path(_home, "config.toml").write_text(MINIMAL_HOME_CONFIG)
Path(_home, "cache.toml").write_text(
    '[update_cache]\nlatest_version = "0.0.0"\nstored_at_timestamp = 0\n'
    'seen_whats_new_version = "0.0.0"\n'
)

env = {"VIBE_HOME": _home, "VIBE_WHATS_NEW_DIR": os.fspath(WHATS_NEW_FIXTURE)}

_URL = (
    "https://admin.mistral.ai/subscriptions/upgrade/plans"
    "?return_to=https%3A%2F%2Fchat.mistral.ai"
)

handshake = {
    "account/read": {
        "account": {
            "status": "ready",
            "plan": {"kind": "api", "name": "FREE", "title": None},
            "planOffer": {"kind": "upgrade_to_pro", "url": _URL},
            "rateLimitAction": None,
        }
    }
}

screen_contains = ("Unlock more with Vibe - Upgrade to Vibe Pro",)
expected_actions = [Action("open_url", _URL)]

# The offer's `Upgrade to Vibe Pro` link starts at one-based column 27 on row 18.
_CLICK = "\x1b[<0;30;18M\x1b[<0;30;18m"

# The banner mounts after startup; typing settles it before the snapshot.
timeline: Timeline = ["x", take_snapshot(), _CLICK]
