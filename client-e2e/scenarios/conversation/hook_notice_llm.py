"""pre_llm and post_llm hook lines each render once, in their own container, in the turn's group."""

from __future__ import annotations

from e2e.app_server.events import (
    assistant_msg,
    hook_completed,
    hook_run_started,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

# Clicks the collapsed group's header row to unfold its hook lines.
_EXPAND_GROUP = "\x1b[<0;1;11M\x1b[<0;1;11m"

timeline: Timeline = [
    "fix the bug\r",
    turn_started(),
    user_msg("fix the bug"),
    hook_run_started("pre_llm", ""),
    hook_completed(
        hook_name="budget-guard",
        content="3 of 20 calls used",
        status="ok",
        scope="pre_llm",
        tool_call_id="",
    ),
    hook_run_started("post_llm", ""),
    hook_completed(
        hook_name="policy-check",
        content="Asked the model to try again: no TODOs in answers",
        status="ok",
        scope="post_llm",
        tool_call_id="",
    ),
    assistant_msg("Fixed."),
    turn_completed(),
    _EXPAND_GROUP,
]

screen_contains = (
    "[budget-guard] 3 of 20 calls used",
    "[policy-check] Asked the model to try again: no TODOs in answers",
)
