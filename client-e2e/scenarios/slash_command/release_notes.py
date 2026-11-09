"""`/release-notes` opens a full-screen pager over every release, newest first, and Esc closes it."""

from __future__ import annotations

import os

from e2e.app_server.config import WHATS_NEW_HISTORY_FIXTURE
from e2e.app_server.scenario import Timeline

env = {"VIBE_WHATS_NEW_DIR": os.fspath(WHATS_NEW_HISTORY_FIXTURE)}

_END = "\x1b[F"

timeline: Timeline = ["/release-notes\r", _END, "\x1b"]

screen_excludes = ("Search release notes", "/ release-notes")
