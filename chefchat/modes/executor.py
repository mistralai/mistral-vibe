"""ChefChat Mode-Aware Tool Executor
====================================

Tool execution wrapper that respects mode permissions.
Extracted from mode_manager.py for better separation of concerns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from chefchat.modes.manager import ModeManager

from chefchat.modes.types import VibeMode


@runtime_checkable
class ToolExecutorProtocol(Protocol):
    """Protocol for tool executors."""

    async def __call__(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool and return the result."""
        ...


class ModeAwareToolExecutor:
    """Wrapper around tool execution that respects mode permissions.

    - Blocks write tools in read-only modes (PLAN, ARCHITECT)
    - Returns helpful error messages with suggestions
    - Truncates output in YOLO mode for speed

    Example:
        original_executor = your_tool_executor
        wrapped = ModeAwareToolExecutor(mode_manager, original_executor)
        result = await wrapped.execute_tool("write_file", {"path": "foo.py"})
    """

    # Maximum result length in YOLO mode
    YOLO_MAX_RESULT_LEN: ClassVar[int] = 500

    def __init__(
        self,
        mode_manager: ModeManager,
        original_executor: Callable[..., Awaitable[dict[str, Any]]],
    ) -> None:
        """Initialize the mode-aware executor.

        Args:
            mode_manager: ModeManager instance for permission checks
            original_executor: The actual tool execution function
        """
        self.mode_manager = mode_manager
        self.original_executor = original_executor

    async def execute_tool(
        self, tool_name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a tool with mode-aware logic.

        Args:
            tool_name: Name of the tool to execute
            args: Arguments for the tool

        Returns:
            Tool execution result, or error dict if blocked
        """
        # Check if tool should be blocked
        blocked, reason = self.mode_manager.should_block_tool(tool_name, args)

        if blocked:
            return {
                "error": True,
                "blocked": True,
                "message": reason,
                "tool": tool_name,
                "mode": self.mode_manager.current_mode.value,
            }

        # Execute the tool
        result = await self.original_executor(tool_name, args)

        # In YOLO mode, truncate large results
        if self.mode_manager.current_mode == VibeMode.YOLO:
            result = self._truncate_for_yolo(result)

        return result

    def _truncate_for_yolo(self, result: dict[str, Any]) -> dict[str, Any]:
        """Truncate result for YOLO mode's minimal output."""
        if not isinstance(result, dict):
            return result

        truncated = {}
        for key, value in result.items():
            if isinstance(value, str) and len(value) > self.YOLO_MAX_RESULT_LEN:
                truncated[key] = value[: self.YOLO_MAX_RESULT_LEN] + "... [truncated]"
            else:
                truncated[key] = value

        return truncated
