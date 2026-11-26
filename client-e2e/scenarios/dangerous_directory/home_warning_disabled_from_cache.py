"""A cached and configured `show_dangerous_directory_warning = false` never shows the warning."""

from __future__ import annotations

import os

from e2e.app_server.config import WORKSPACE_DIR
from e2e.app_server.scenario import Timeline
from scenarios.dangerous_directory._cache import cached_home

env = {"HOME": os.fspath(WORKSPACE_DIR), "VIBE_HOME": cached_home(show_warning=False)}

handshake = {
    "runtime/read": {"runtime": {"config": {"showDangerousDirectoryWarning": False}}}
}

timeline: Timeline = []

screen_excludes = ("home directory",)
