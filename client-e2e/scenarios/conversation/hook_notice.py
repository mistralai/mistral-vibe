"""Completed hook notices render their output line inside the tool's hook container."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    bash,
    hook_completed,
    hook_run_started,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_EFFECT_ID = "hooked-bash"

timeline: Timeline = [
    "lint the repo\r",
    turn_started(),
    user_msg("lint the repo"),
    bash("ruff check .", stdout="all clean", entry_id=_EFFECT_ID),
    hook_run_started("post_tool", _EFFECT_ID),
    hook_completed(
        hook_name="lint-reporter",
        content="12 files checked, no issues",
        status="ok",
        scope="post_tool",
        tool_call_id=_EFFECT_ID,
    ),
    assistant_msg("Lint passed."),
    turn_completed(),
]
