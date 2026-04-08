from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from vibe.core.memory.manager import MEMORY_TYPES, MemoryManager
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


class MemoryWriteArgs(BaseModel):
    name: str = Field(
        description="Short identifier for the memory (e.g., 'user_prefers_tabs')."
    )
    type: str = Field(description="Category: feedback, user, project, or reference.")
    description: str = Field(
        description="One-line summary of what this memory is about."
    )
    content: str = Field(description="The full memory content to save.")
    scope: str = Field(
        default="project",
        description="Where to save: 'project' (.vibe/memory/) or 'global' (~/.vibe/memory/).",
    )


class MemoryWriteResult(BaseModel):
    path: str = Field(description="Path where the memory was saved.")
    name: str = Field(description="Name of the saved memory.")


class MemoryWriteConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK


class MemoryWrite(
    BaseTool[MemoryWriteArgs, MemoryWriteResult, MemoryWriteConfig, BaseToolState],
    ToolUIData[MemoryWriteArgs, MemoryWriteResult],
):
    description: ClassVar[str] = (
        "Save a memory that persists across sessions. Use this to remember user preferences, "
        "project patterns, feedback, or important context. Memories are loaded automatically "
        "at the start of each session."
    )

    async def run(
        self, args: MemoryWriteArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | MemoryWriteResult, None]:
        if not args.name.strip():
            raise ToolError("Memory name cannot be empty.")
        if not args.content.strip():
            raise ToolError("Memory content cannot be empty.")
        if args.type not in MEMORY_TYPES:
            raise ToolError(
                f"Invalid memory type: {args.type}. Must be one of {MEMORY_TYPES}"
            )
        if args.scope not in {"project", "global"}:
            raise ToolError("Scope must be 'project' or 'global'.")

        manager = MemoryManager()
        try:
            path = manager.write_memory(
                name=args.name,
                type=args.type,
                description=args.description,
                content=args.content,
                scope=args.scope,
            )
        except Exception as exc:
            raise ToolError(f"Failed to save memory: {exc}") from exc

        yield MemoryWriteResult(path=str(path), name=args.name)

    @classmethod
    def format_call_display(cls, args: MemoryWriteArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"Saving memory: {args.name}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, MemoryWriteResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )
        return ToolResultDisplay(
            success=True, message=f"Memory '{event.result.name}' saved"
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Saving memory"
