"""The mcpServer variant of the Harness ProvidedToolExecutor contract."""

from __future__ import annotations

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


class McpToolExecutor:
    def __init__(self, runtime: MCPRuntime) -> None:
        self._runtime = runtime

    async def __call__(
        self, action: RustProvidedToolCallAction
    ) -> RustToolSucceededEvent | RustToolFailedEvent:
        async def call(
            group_name: str, tool_name: str, arguments: dict
        ) -> MCPNormalizedResult:
            return await self._runtime.execute(
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


__all__ = ["McpToolExecutor"]
