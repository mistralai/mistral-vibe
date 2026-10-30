"""`vibe-acp --setup`: initialize reports `acp` and a pending update never prompts."""

from __future__ import annotations

import atexit
from pathlib import Path
import shutil
import tempfile

from e2e.app_server.config import MINIMAL_HOME_CONFIG, REPLAY_NOW_MS
from e2e.app_server.scenario import Timeline

# The same pending update `update/prompt.py` prompts for; an IDE setup skips it.
_home = tempfile.mkdtemp(prefix="e2e_onboarding_acp_entrypoint_")
atexit.register(shutil.rmtree, _home, True)
Path(_home, "config.toml").write_text(MINIMAL_HOME_CONFIG)
Path(_home, "cache.toml").write_text(
    "[update_cache]\n"
    'latest_version = "99.0.0"\n'
    f"stored_at_timestamp = {REPLAY_NOW_MS // 1000}\n"
)

client_args = ("--setup",)
env = {"VIBE_HOME": _home, "VIBE_SETUP_ENTRYPOINT": "acp"}
request_methods = {"initialize", "setup/status"}
timeline: Timeline = []

screen_excludes = ("A new Vibe release is available",)
