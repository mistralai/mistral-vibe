"""A cached `show_dangerous_directory_warning = false` yields to a config that turned it back on."""

from __future__ import annotations

import os

from e2e.app_server.config import WORKSPACE_DIR
from e2e.app_server.scenario import Timeline
from scenarios.dangerous_directory._cache import cached_home

env = {"HOME": os.fspath(WORKSPACE_DIR), "VIBE_HOME": cached_home(show_warning=False)}

timeline: Timeline = []

screen_contains = ("You are in the home directory",)
