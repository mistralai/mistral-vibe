"""Translate connector calls into generic Harness provided-tool events."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from mistralai_vibe_local_harness.protocol import (
    RustProvidedToolCallAction,
    RustToolFailedEvent,
    RustToolSucceededEvent,
)
from mistralai_vibe_local_harness.vibe._connector_models import (
    ConnectorNormalizedResult,
    ConnectorRuntimeFailure,
)
from mistralai_vibe_local_harness.vibe._connector_runtime import ConnectorRuntime
from mistralai_vibe_local_harness.vibe._provided_tool_actions import (
    execute_provided_tool_action,
)

type ConnectorActionExecutor = Callable[
    [RustProvidedToolCallAction],
    Awaitable[RustToolSucceededEvent | RustToolFailedEvent],
]


def build_connector_action_executor(
    runtime: ConnectorRuntime,
) -> ConnectorActionExecutor:
    async def execute(
        action: RustProvidedToolCallAction,
    ) -> RustToolSucceededEvent | RustToolFailedEvent:
        async def call(
            group_name: str, tool_name: str, arguments: dict
        ) -> ConnectorNormalizedResult:
            return await runtime.execute(
                group_name=group_name, tool_name=tool_name, arguments=arguments
            )

        return await execute_provided_tool_action(
            action,
            call,
            failure_type=ConnectorRuntimeFailure,
            invalid_result_code="connector_invalid_result",
            tool_error_code="connector_tool_error",
            tool_error_message="The connector tool returned an error",
        )

    return execute


__all__ = ["ConnectorActionExecutor", "build_connector_action_executor"]
