from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mistralai_vibe_local_harness.protocol import (
    RustProtocolError,
    RustRuntimeBuiltinToolCallAction,
    RustToolFailedEvent,
    RustToolFailureResult,
    RustToolSucceededEvent,
    RustToolSuccessResult,
)


class SleepArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    seconds: float = Field(gt=0, lt=60, allow_inf_nan=False)


async def execute_self_tool(
    action: RustRuntimeBuiltinToolCallAction,
) -> RustToolSucceededEvent | RustToolFailedEvent:
    try:
        if action.call.name != "self.sleep":
            raise ValueError(f"Unsupported self tool: {action.call.name}")
        args = SleepArgs.model_validate(action.call.arguments)
        await asyncio.sleep(args.seconds)
        return RustToolSucceededEvent(
            action_id=action.action_id,
            call_id=action.call_id,
            result=RustToolSuccessResult(structured_content={"seconds": args.seconds}),
        )
    except asyncio.CancelledError:
        raise
    except (ValidationError, ValueError) as exc:
        return RustToolFailedEvent(
            action_id=action.action_id,
            call_id=action.call_id,
            result=RustToolFailureResult(
                error=RustProtocolError(
                    code="tool_failed", message=str(exc), retryable=False, details=None
                )
            ),
        )


__all__ = ["execute_self_tool"]
