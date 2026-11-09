from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import time
from typing import Literal

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

    seconds: float = Field(gt=0, allow_inf_nan=False)


class WaitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    timeout_ms: int = Field(gt=0)
    deadline_unix_ms: int = Field(ge=0)


# Returns None when committed model input is pending, otherwise an Event that is
# set when the Runtime next commits Core input.
type ModelInputSignal = Callable[[], Awaitable[asyncio.Event | None]]


async def execute_model_wait(
    action: RustRuntimeBuiltinToolCallAction,
    input_signal: ModelInputSignal,
    *,
    max_wait_ms: int,
    recovering: bool,
) -> RustToolSucceededEvent | RustToolFailedEvent:
    """Wait for committed Session input or the action's absolute deadline.

    The wait is read-only: it neither consumes nor copies input. Core commits
    the pending input into model context once this result is applied.

    A wait recovered after a restart never blocks: it answers from the Session's
    current state at once (``input_available`` or ``timeout``), so resuming the
    Session is not held until the old deadline. The model can call ``wait`` again.

    Core already caps ``timeout_ms`` in the tool schema. The local clamp to
    ``max_wait_ms`` keeps a malformed journal entry from blocking past it.
    """
    try:
        args = WaitArgs.model_validate(action.call.arguments)
    except ValidationError as exc:
        return _failed(action, str(exc))
    deadline_unix_ms = min(
        args.deadline_unix_ms, _now_unix_ms() + min(args.timeout_ms, max_wait_ms)
    )
    while True:
        committed = await input_signal()
        if committed is None:
            return _waited(action, "input_available")
        remaining_ms = deadline_unix_ms - _now_unix_ms()
        if recovering or remaining_ms <= 0:
            return _waited(action, "timeout")
        try:
            await asyncio.wait_for(committed.wait(), timeout=remaining_ms / 1000)
        except TimeoutError:
            continue


def _now_unix_ms() -> int:
    return time.time_ns() // 1_000_000


def _waited(
    action: RustRuntimeBuiltinToolCallAction,
    reason: Literal["input_available", "timeout"],
) -> RustToolSucceededEvent:
    return RustToolSucceededEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolSuccessResult(structured_content={"reason": reason}),
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


async def execute_self_tool(
    action: RustRuntimeBuiltinToolCallAction, *, max_sleep_ms: int
) -> RustToolSucceededEvent | RustToolFailedEvent:
    """Run ``self.sleep`` for at most ``settings.tools.programmatic.max_sleep_ms``.

    Core already caps ``seconds`` in the tool schema. The local check keeps a
    malformed journal entry from sleeping past it.
    """
    try:
        if action.call.name != "self.sleep":
            raise ValueError(f"Unsupported self tool: {action.call.name}")
        args = SleepArgs.model_validate(action.call.arguments)
        if args.seconds > max_sleep_ms / 1000:
            raise ValueError(
                f"Sleep exceeds the Runtime's per-call limit of {max_sleep_ms} ms"
            )
        await asyncio.sleep(args.seconds)
        return RustToolSucceededEvent(
            action_id=action.action_id,
            call_id=action.call_id,
            result=RustToolSuccessResult(structured_content={"seconds": args.seconds}),
        )
    except asyncio.CancelledError:
        raise
    except (ValidationError, ValueError) as exc:
        return _failed(action, str(exc))


__all__ = ["ModelInputSignal", "execute_model_wait", "execute_self_tool"]
