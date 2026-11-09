"""Translate Runtime-owned MCP calls into generic Harness tool events."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from mistralai_vibe_local_harness.protocol import (
    RustProvidedToolCallAction,
    RustToolFailedEvent,
    RustToolSucceededEvent,
)
from mistralai_vibe_local_harness.vibe._mcp_models import (
    MCPNormalizedResult,
    MCPRuntimeFailure,
)
from mistralai_vibe_local_harness.vibe._mcp_runtime import MCPRuntime
from mistralai_vibe_local_harness.vibe._provided_tool_actions import (
    execute_provided_tool_action,
)

type MCPActionExecutor = Callable[
    [RustProvidedToolCallAction],
    Awaitable[RustToolSucceededEvent | RustToolFailedEvent],
]


def build_mcp_action_executor(runtime: MCPRuntime) -> MCPActionExecutor:
    async def execute(
        action: RustProvidedToolCallAction,
    ) -> RustToolSucceededEvent | RustToolFailedEvent:
        async def call(
            group_name: str, tool_name: str, arguments: dict
        ) -> MCPNormalizedResult:
            return await runtime.execute(
                group_name=group_name, tool_name=tool_name, arguments=arguments
            )

        return await execute_provided_tool_action(
            action,
            call,
            failure_type=MCPRuntimeFailure,
            invalid_result_code="mcp_invalid_result",
            tool_error_code="mcp_tool_error",
            tool_error_message="The MCP tool returned an error",
        )

    return execute


__all__ = ["MCPActionExecutor", "build_mcp_action_executor"]
