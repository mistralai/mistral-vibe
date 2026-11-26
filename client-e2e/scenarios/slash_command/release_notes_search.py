"""`/whatsnew` opens the release notes; `/` then typing keeps only the matching releases."""

from __future__ import annotations

import os

from e2e.app_server.config import WHATS_NEW_HISTORY_FIXTURE
from e2e.app_server.scenario import Timeline

env = {"VIBE_WHATS_NEW_DIR": os.fspath(WHATS_NEW_HISTORY_FIXTURE)}

timeline: Timeline = ["/whatsnew\r", "/teleport", "\x1b", "/zzz"]

screen_contains = ("No release notes match your search.",)
