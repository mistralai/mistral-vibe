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


class BashOutputArgs(BaseModel):
    shell_id: str = Field(description="Id of the background shell, e.g. 'bg_1'.")


class BashOutputResult(BaseModel):
    shell_id: str
    stdout: str
    stderr: str
    running: bool
    returncode: int | None = None


class BashOutputConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class BashOutput(
    BaseTool[BashOutputArgs, BashOutputResult, BashOutputConfig, BaseToolState],
    ToolUIData[BashOutputArgs, BashOutputResult],
):
    description: ClassVar[str] = (
        "Read new output from a background shell started with bash(run_in_background=true). "
        "Returns only output produced since the last read, plus whether the shell is "
        "still running."
    )

    async def run(
        self, args: BashOutputArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | BashOutputResult, None]:
        shell = get_background_shell_registry().get(args.shell_id)
        if shell is None:
            raise ToolError(f"No background shell with id {args.shell_id!r}.")

        stdout, stderr = shell.drain_new_output()
        yield BashOutputResult(
            shell_id=shell.id,
            stdout=stdout,
            stderr=stderr,
            running=shell.running,
            returncode=shell.returncode,
        )

    @classmethod
    def format_call_display(cls, args: BashOutputArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"bash output: {args.shell_id}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, BashOutputResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )
        state = "running" if event.result.running else "exited"
        return ToolResultDisplay(
            success=True, message=f"{event.result.shell_id} ({state})"
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Reading background output"
