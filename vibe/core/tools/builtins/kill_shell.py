from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from vibe.core.tools.background_shells import get_background_shell_registry
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import ToolStreamEvent

if TYPE_CHECKING:
    from vibe.core.types import ToolResultEvent


class KillShellArgs(BaseModel):
    shell_id: str = Field(description="Id of the background shell to terminate.")


class KillShellResult(BaseModel):
    shell_id: str
    killed: bool


class KillShellConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class KillShell(
    BaseTool[KillShellArgs, KillShellResult, KillShellConfig, BaseToolState],
    ToolUIData[KillShellArgs, KillShellResult],
):
    description: ClassVar[str] = (
        "Terminate a background shell started with bash(run_in_background=true)."
    )

    async def run(
        self, args: KillShellArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | KillShellResult, None]:
        registry = get_background_shell_registry()
        if registry.get(args.shell_id) is None:
            raise ToolError(f"No background shell with id {args.shell_id!r}.")

        killed = await registry.kill(args.shell_id)
        registry.remove(args.shell_id)
        yield KillShellResult(shell_id=args.shell_id, killed=killed)

    @classmethod
    def format_call_display(cls, args: KillShellArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"kill shell: {args.shell_id}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, KillShellResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )
        return ToolResultDisplay(
            success=True, message=f"Killed {event.result.shell_id}"
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Stopping background shell"
