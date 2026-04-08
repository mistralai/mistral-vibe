from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, ClassVar

from pydantic import BaseModel, Field

from vibe.core.memory.manager import MemoryManager
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


class MemoryReadArgs(BaseModel):
    name: str | None = Field(
        default=None,
        description="Read a specific memory by name. If omitted, lists all memories.",
    )
    scope: str = Field(
        default="all",
        description="Which memories to read: 'all', 'project', or 'global'.",
    )


class MemoryEntry(BaseModel):
    name: str
    type: str
    description: str
    content: str


class MemoryReadResult(BaseModel):
    memories: list[MemoryEntry] = Field(description="The memories found.")
    total_count: int = Field(description="Total number of memories returned.")


class MemoryReadConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class MemoryRead(
    BaseTool[MemoryReadArgs, MemoryReadResult, MemoryReadConfig, BaseToolState],
    ToolUIData[MemoryReadArgs, MemoryReadResult],
):
    description: ClassVar[str] = (
        "Read memories saved from previous sessions. Use this to recall user preferences, "
        "project patterns, past feedback, or important context."
    )

    async def run(
        self, args: MemoryReadArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | MemoryReadResult, None]:
        manager = MemoryManager()

        if args.name:
            mem = manager.read_memory(args.name)
            if not mem:
                raise ToolError(f"Memory not found: {args.name}")
            entries = [
                MemoryEntry(
                    name=mem.name,
                    type=mem.type,
                    description=mem.description,
                    content=mem.content,
                )
            ]
        else:
            memories = manager.list_memories(scope=args.scope)
            entries = [
                MemoryEntry(
                    name=m.name,
                    type=m.type,
                    description=m.description,
                    content=m.content,
                )
                for m in memories
            ]

        yield MemoryReadResult(memories=entries, total_count=len(entries))

    @classmethod
    def format_call_display(cls, args: MemoryReadArgs) -> ToolCallDisplay:
        if args.name:
            return ToolCallDisplay(summary=f"Reading memory: {args.name}")
        return ToolCallDisplay(summary="Reading all memories")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, MemoryReadResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )
        count = event.result.total_count
        word = "memory" if count == 1 else "memories"
        return ToolResultDisplay(success=True, message=f"Found {count} {word}")

    @classmethod
    def get_status_text(cls) -> str:
        return "Reading memories"
