"""A pre_llm hook that ends the turn after a tool call settles the tool group: no spinner is left."""

from __future__ import annotations

from e2e.app_server.events import (
    bash,
    hook_completed,
    hook_run_started,
    turn_completed,
    turn_started,
    user_msg,
)
from e2e.app_server.scenario import Timeline

_PROMPT = "run the tests"
_STOPPED = turn_completed()
_STOPPED["params"]["turn"]["skipReason"] = "Budget of 1 model call per turn reached"

timeline: Timeline = [
    f"{_PROMPT}\r",
    turn_started(),
    user_msg(_PROMPT),
    bash("make test", stdout="ok"),
    hook_run_started("pre_llm", ""),
    hook_completed(
        hook_name="budget",
        content="Stopped the turn: Budget of 1 model call per turn reached",
        status="error",
        scope="pre_llm",
        tool_call_id="",
    ),
    _STOPPED,
]

screen_contains = ("Ran 1 command",)
screen_excludes = ("Running 1 command",)
