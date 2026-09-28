from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

from mistralai_vibe_local_harness.protocol import (
    RustProtocolError,
    RustRuntimeBuiltinToolCallAction,
    RustTextContentBlock,
    RustToolFailedEvent,
    RustToolFailureResult,
    RustToolSucceededEvent,
    RustToolSuccessResult,
)
from mistralai_vibe_local_harness.vibe._runtime_config import LocalRuntimeAdapterConfig


class SkillArgs(BaseModel):
    name: str = Field(min_length=1)


async def execute_skill_tool(
    action: RustRuntimeBuiltinToolCallAction, config: LocalRuntimeAdapterConfig
) -> RustToolSucceededEvent | RustToolFailedEvent:
    try:
        if action.call.name != "skill.read":
            raise ValueError(f"Unsupported skill tool: {action.call.name}")
        args = SkillArgs.model_validate(action.call.arguments)
    except (ValidationError, ValueError) as exc:
        return _failed(action, str(exc))

    body = config.skills.get(args.name)
    if body is None:
        available = ", ".join(sorted(config.skills))
        return _failed(
            action,
            f'Skill "{args.name}" is not available to this runtime. '
            f"Available skills: {available or 'none'}",
        )
    return _succeeded(action, body)


def _succeeded(
    action: RustRuntimeBuiltinToolCallAction, body: str
) -> RustToolSucceededEvent:
    return RustToolSucceededEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolSuccessResult(
            content=[RustTextContentBlock(text=body)], structured_content=body
        ),
    )


def _failed(
    action: RustRuntimeBuiltinToolCallAction, message: str
) -> RustToolFailedEvent:
    return RustToolFailedEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolFailureResult(
            error=RustProtocolError(
                code="tool_failed", message=message, retryable=False, details=None
            )
        ),
    )


__all__ = ["execute_skill_tool"]
