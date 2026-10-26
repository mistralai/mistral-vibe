"""Fail restored retired Actions without reinterpreting their original arguments."""

from __future__ import annotations

from mistralai_vibe_local_harness.protocol import (
    RustProtocolError,
    RustRuntimeBuiltinToolCallAction,
    RustRuntimeBuiltinToolName,
    RustTextContentBlock,
    RustToolFailedEvent,
    RustToolFailureResult,
)

# Deprecated: retired names, kept only to fail restored Actions with a
# migration hint.
_MIGRATION_GUIDANCE: dict[RustRuntimeBuiltinToolName, str] = {
    "subagent.list": "Use tools.agents.list.",
    "subagent.spawn": "Use tools.agents.spawn.",
    "subagent.send_message": "Use tools.agents.send_message.",
    "subagent.interrupt": "Use tools.agents.interrupt.",
    "subagent.wait": "Use standalone wait outside run_typescript.",
    "subagent.stop": "No replacement exists for destructive stop or close.",
    "file_system.search_replace": "Use edit, or tools.file_system.edit in run_typescript.",
}


def deprecated_tool_failure(
    action: RustRuntimeBuiltinToolCallAction,
) -> RustToolFailedEvent:
    """Fail a restored Action that names a retired tool.

    Deprecated: replay-only compatibility for sessions persisted before the
    rename; there is nothing to migrate to here.
    """
    message = (
        f"{action.call.name} is deprecated and cannot execute. "
        f"{_MIGRATION_GUIDANCE[action.call.name]}"
    )
    return RustToolFailedEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolFailureResult(
            content=[RustTextContentBlock(text=message)],
            error=RustProtocolError(
                code="deprecated_tool", message=message, retryable=False
            ),
        ),
    )
