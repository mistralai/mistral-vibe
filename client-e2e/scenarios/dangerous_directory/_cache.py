"""A `VIBE_HOME` whose startup cache holds a given `show_dangerous_directory_warning`."""

from __future__ import annotations

import atexit
import json
from pathlib import Path
import re
import shutil
import tempfile
import time

from e2e.app_server.config import MINIMAL_HOME_CONFIG, VIBE_DIR

_CLI = VIBE_DIR / "vibe" / "cli-rust"


def cached_home(show_warning: bool) -> str:
    """Return a temp `VIBE_HOME` seeded with a fresh startup cache."""
    version = re.search(
        r'^version = "([^"]+)"', (_CLI / "Cargo.toml").read_text(), re.MULTILINE
    )
    schema = re.search(
        r"const SCHEMA_VERSION: u32 = (\d+);",
        (_CLI / "src" / "utils" / "startup_cache.rs").read_text(),
    )
    assert version is not None and schema is not None
    home = tempfile.mkdtemp(prefix="e2e_dangerous_directory_cached_")
    atexit.register(shutil.rmtree, home, True)
    Path(home, "config.toml").write_text(MINIMAL_HOME_CONFIG)
    Path(home, "ui_startup_config.json").write_text(
        json.dumps({
            "schemaVersion": int(schema.group(1)),
            "serverVersion": version.group(1),
            "theme": "ansi-dark",
            "disableWelcomeBannerAnimation": True,
            "autocopyToClipboard": True,
            "showDangerousDirectoryWarning": show_warning,
            "activeModel": "mistral-medium-3.5",
            "activeModelDisplayName": "mistral-medium-3.5",
            "imagesSupported": False,
            "defaultAgent": "default",
            "agents": [],
            "defaultApprovalPolicy": "accept-edits",
            "approvalPolicies": [],
            "skills": [],
            "contextWindow": 0,
            "narratorEnabled": False,
            "cachedAt": int(time.time()),
        })
    )
    return home
