"""Generic provided-tool result mapping shared by Runtime integrations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import JsonValue, TypeAdapter, ValidationError

from mistralai_vibe_local_harness.protocol import (
    RustContentBlock,
    RustProtocolError,
    RustProvidedToolCallAction,
    RustToolFailedEvent,
    RustToolFailureResult,
    RustToolSucceededEvent,
    RustToolSuccessResult,
)
from mistralai_vibe_local_harness.vibe._connector_models import (
    ConnectorNormalizedResult,
)
from mistralai_vibe_local_harness.vibe._mcp_models import MCPNormalizedResult

_CONTENT_BLOCKS = TypeAdapter(list[RustContentBlock])


type ProvidedToolCall = Callable[
    [str, str, dict[str, JsonValue]],
    Awaitable[MCPNormalizedResult | ConnectorNormalizedResult],
]


async def execute_provided_tool_action(
    action: RustProvidedToolCallAction,
    call: ProvidedToolCall,
    *,
    failure_type: type[Exception],
    invalid_result_code: str,
    tool_error_code: str,
    tool_error_message: str,
) -> RustToolSucceededEvent | RustToolFailedEvent:
    try:
        result = await call(
            action.call.group_name, action.call.tool_name, action.call.arguments
        )
        content = _CONTENT_BLOCKS.validate_python(list(result.content))
    except failure_type as exc:
        return _failed(
            action,
            code=str(getattr(exc, "code", invalid_result_code)),
            retryable=bool(getattr(exc, "retryable", False)),
        )
    except (ValidationError, ValueError, TypeError):
        return _failed(action, code=invalid_result_code, retryable=False)
    if result.is_error:
        return RustToolFailedEvent(
            action_id=action.action_id,
            call_id=action.call_id,
            result=RustToolFailureResult(
                content=content,
                structured_content=result.structured_content,
                _meta=result.meta,
                error=RustProtocolError(
                    code=tool_error_code,
                    message=tool_error_message,
                    retryable=False,
                    details=None,
                ),
            ),
        )
    return RustToolSucceededEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolSuccessResult(
            content=content,
            structured_content=result.structured_content,
            _meta=result.meta,
        ),
    )


def _failed(
    action: RustProvidedToolCallAction, *, code: str, retryable: bool
) -> RustToolFailedEvent:
    return RustToolFailedEvent(
        action_id=action.action_id,
        call_id=action.call_id,
        result=RustToolFailureResult(
            error=RustProtocolError(
                code=code,
                message="The provided tool call failed",
                retryable=retryable,
                details=None,
            )
        ),
    )


__all__ = ["ProvidedToolCall", "execute_provided_tool_action"]
