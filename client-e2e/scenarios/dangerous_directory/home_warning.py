"""Launched from the home directory, the startup warns about the location."""

from __future__ import annotations

import os

from e2e.app_server.config import WORKSPACE_DIR
from e2e.app_server.scenario import Timeline

env = {"HOME": os.fspath(WORKSPACE_DIR)}

timeline: Timeline = []

screen_contains = ("You are in the home directory",)
