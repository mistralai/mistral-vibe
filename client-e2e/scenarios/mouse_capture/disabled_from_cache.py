"""A cached `enable_mouse_capture = false` keeps mouse reports off from the very first frame."""

from __future__ import annotations

import atexit
import json
from pathlib import Path
import re
import shutil
import tempfile
import time

from e2e.app_server.config import MINIMAL_HOME_CONFIG, VIBE_DIR
from e2e.app_server.scenario import Timeline

_CLI = VIBE_DIR / "vibe" / "cli-rust"
_VERSION = re.search(
    r'^version = "([^"]+)"', (_CLI / "Cargo.toml").read_text(), re.MULTILINE
)
_SCHEMA = re.search(
    r"const SCHEMA_VERSION: u32 = (\d+);",
    (_CLI / "src" / "utils" / "startup_cache.rs").read_text(),
)
assert _VERSION is not None and _SCHEMA is not None

# The previous run cached the setting, which the client reads before it owns the terminal.
_home = tempfile.mkdtemp(prefix="e2e_mouse_capture_cached_")
atexit.register(shutil.rmtree, _home, True)
Path(_home, "config.toml").write_text(MINIMAL_HOME_CONFIG)
Path(_home, "ui_startup_config.json").write_text(
    json.dumps({
        "schemaVersion": int(_SCHEMA.group(1)),
        "serverVersion": _VERSION.group(1),
        "theme": "ansi-dark",
        "disableWelcomeBannerAnimation": True,
        "autocopyToClipboard": True,
        "enableMouseCapture": False,
        "activeModel": "mistral-medium-3.5",
        "activeModelDisplayName": "mistral-medium-3.5",
        "imagesSupported": False,
        "defaultAgent": "accept-edits",
        "agents": [],
        "skills": [],
        "contextWindow": 0,
        "narratorEnabled": False,
        "cachedAt": int(time.time()),
    })
)

_CONFIG = {"enableMouseCapture": False}

env = {"VIBE_HOME": _home}
handshake = {
    "config/read": {"config": _CONFIG},
    "runtime/read": {"runtime": {"config": _CONFIG}},
}
mouse_tracking = False
mouse_tracking_seen = False
timeline: Timeline = []
