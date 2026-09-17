"""A cached old version shows the what's-new body under the messages area."""

from __future__ import annotations

import atexit
from pathlib import Path
import shutil
import tempfile

from e2e.app_server.config import MINIMAL_HOME_CONFIG
from e2e.app_server.scenario import Timeline

# The harness's isolated home is fresh, so the what's-new gate never trips;
# pin a VIBE_HOME seeded with a stale seen-version instead. The rust client
# must read its own seen-version store from this home as well.
_home = tempfile.mkdtemp(prefix="e2e_whatsnew_")
# Both captures of a session must read the same seeded home, so clean it up
# only when the pytest session exits.
atexit.register(shutil.rmtree, _home, True)
Path(_home, "config.toml").write_text(MINIMAL_HOME_CONFIG)
Path(_home, "cache.toml").write_text(
    '[update_cache]\nlatest_version = "0.0.0"\nstored_at_timestamp = 0\n'
    'seen_whats_new_version = "0.0.0"\n'
    # The rust client keeps its own seen-version section in the same file so the
    # first capture's write cannot close the other client's gate.
    '[whats_new]\nseen_version = "0.0.0"\n'
)

env = {"VIBE_HOME": _home}

# The banner mounts after startup; the capture after typing settles it.
capture_startup = False
timeline: Timeline = ["x"]
