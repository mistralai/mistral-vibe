"""An `@/` path lists that directory and filters it by the typed name prefix."""

from __future__ import annotations

from e2e.app_server.config import WORKSPACE_DIR
from e2e.app_server.scenario import Timeline

_DIR = f"{WORKSPACE_DIR}/at_popup/"

expected_clipboard = f"@{_DIR}beta.md ready"

timeline: Timeline = [f"@{_DIR}be", "\r", "ready\x1b[18~\x19"]
