"""Parallel calls' pre-tool hook lines sit above their own tool, matched by provider call id."""

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
from e2e.app_server.scenario import AppServerEvent, Timeline, take_snapshot


def _bash_call(command: str, call_id: str) -> AppServerEvent:
    event = bash(command, stdout="ok", entry_id=f"effect-{call_id}")
    event["params"]["entry"]["toolCallId"] = call_id
    return event


def _pre_tool_hook(call_id: str, content: str) -> list[AppServerEvent]:
    return [
        hook_run_started("pre_tool", call_id),
        hook_completed(
            hook_name="guard",
            content=content,
            status="ok",
            scope="pre_tool",
            tool_call_id=call_id,
        ),
    ]


timeline: Timeline = [
    "list twice\r",
    turn_started(),
    user_msg("list twice"),
    # Core runs every pre-tool hook of the batch before any of its tools starts.
    *_pre_tool_hook("call-1", "checked ls"),
    *_pre_tool_hook("call-2", "checked ls -a"),
    _bash_call("ls", "call-1"),
    _bash_call("ls -a", "call-2"),
    assistant_msg("Listed."),
    turn_completed(),
    "\x0f",
    take_snapshot(),
]
