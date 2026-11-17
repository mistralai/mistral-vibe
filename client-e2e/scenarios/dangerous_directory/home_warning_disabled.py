"""With `show_dangerous_directory_warning = false`, the home directory gets no warning."""

from __future__ import annotations

import os

from e2e.app_server.config import WORKSPACE_DIR
from e2e.app_server.scenario import Timeline

env = {"HOME": os.fspath(WORKSPACE_DIR)}

handshake = {
    "runtime/read": {"runtime": {"config": {"showDangerousDirectoryWarning": False}}}
}

timeline: Timeline = []

screen_excludes = ("home directory",)
