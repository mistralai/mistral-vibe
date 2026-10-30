"""Every release since the last seen one shows at startup, capped, with a pointer to the rest."""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import tempfile

from e2e.app_server.config import MINIMAL_HOME_CONFIG, WHATS_NEW_HISTORY_FIXTURE
from e2e.app_server.scenario import Timeline

# The user last saw v9.1.0: the six newer releases are unseen, five show.
_home = tempfile.mkdtemp(prefix="e2e_whatsnew_since_")
atexit.register(shutil.rmtree, _home, True)
Path(_home, "config.toml").write_text(MINIMAL_HOME_CONFIG)
Path(_home, "cache.toml").write_text(
    '[update_cache]\nlatest_version = "0.0.0"\nstored_at_timestamp = 0\n'
    'seen_whats_new_version = "9.1.0"\n'
)

env = {"VIBE_HOME": _home, "VIBE_WHATS_NEW_DIR": os.fspath(WHATS_NEW_HISTORY_FIXTURE)}

screen_contains = ("Release notes", "v9.9.9", "v9.4.0", "2 earlier releases not shown")
screen_excludes = ("v9.3.0", "already seen")

# The banner mounts after startup; the capture after typing settles it.
capture_startup = False
timeline: Timeline = ["x"]
