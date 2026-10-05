"""A pending cached update prompts at startup; continue reaches the TUI."""

from __future__ import annotations

import atexit
from pathlib import Path
import shutil
import tempfile

from e2e.app_server.config import MINIMAL_HOME_CONFIG, REPLAY_NOW_MS
from e2e.app_server.scenario import Timeline

# The startup prompt is cache-driven: seed a pending newer version in a pinned
# VIBE_HOME, fresh enough that the pending-update query returns it. The dialog
# dismisses on continue by writing the cache, so the home must survive both
# captures of a session; clean it up only when the pytest session exits.
_home = tempfile.mkdtemp(prefix="e2e_update_prompt_")
atexit.register(shutil.rmtree, _home, True)
Path(_home, "config.toml").write_text(MINIMAL_HOME_CONFIG)
Path(_home, "cache.toml").write_text(
    "[update_cache]\n"
    'latest_version = "99.0.0"\n'
    f"stored_at_timestamp = {REPLAY_NOW_MS // 1000}\n"
    # The what's-new gate reads this section too; a seen version equal to the
    # client's keeps its banner out of this scenario's TUI frame. Bump this
    # with cli-rust's Cargo.toml version, or the gate reopens and the golden
    # gains the what's-new banner.
    'seen_whats_new_version = "2.25.8"\n'
)

env = {"VIBE_HOME": _home}

# The harness's startup settle lands on the dialog's first frame. Right moves
# to Continue, Enter dismisses the dialog (the settle then lands on the TUI's
# own first marker), and "x" proves the session input is live.
capture_startup = False
timeline: Timeline = ["\x1b[C", "\r", "x"]

screen_excludes = {"rust": ("A new Vibe release is available",)}
