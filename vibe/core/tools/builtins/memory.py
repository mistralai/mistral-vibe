from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from vibe.core.tools.base import BaseTool, BaseToolConfig


class MemoryToolConfig(BaseToolConfig):
    """Configuration for memory management tool."""
    name: str = "memory"

class ListMemoriesArgs(BaseModel):
    """Arguments for listing memories."""
    pass


class ListMemoriesResult(BaseModel):
    """Result of listing memories."""
    memories: list[dict] = Field(
        ...,
        description="List of available memories with their metadata"
    )


class AddMemoryArgs(BaseModel):
    """Arguments for adding a new memory."""
    content: str = Field(
        ...,
        description="The content of the memory in markdown format"
    )
    triggers: list[str] = Field(
        ...,
        description="List of triggers for when to load this memory (e.g., ['always', 'tool_use:grep'])"
    )
    title: str | None = Field(
        None,
        description="Optional title for the memory"
    )
    priority: int = Field(
        0,
        description="Priority for memory loading (higher numbers load first)"
    )


class AddMemoryResult(BaseModel):
    """Result of adding a memory."""
    success: bool = Field(
        ...,
        description="Whether the memory was successfully added"
    )
    memory_path: str | None = Field(
        None,
        description="Path to the created memory file"
    )
    error: str | None = Field(
        None,
        description="Error message if addition failed"
    )


class RemoveMemoryArgs(BaseModel):
    """Arguments for removing a memory."""
    memory_id: int = Field(
        ...,
        description="Index of the memory to remove (from list_memories result)"
    )


class RemoveMemoryResult(BaseModel):
    """Result of removing a memory."""
    success: bool = Field(
        ...,
        description="Whether the memory was successfully removed"
    )
    removed_path: str | None = Field(
        None,
        description="Path to the removed memory file"
    )
    error: str | None = Field(
        None,
        description="Error message if removal failed"
    )


class MemoryTool(BaseTool[MemoryToolConfig]):
    """Tool for managing memories in the current session."""

    def __init__(self, config: MemoryToolConfig) -> None:
        super().__init__(config)
        self._memory_manager = None

    @property
    def memory_manager(self):
        """Get the memory manager from the agent loop."""
        if self._memory_manager is None:
            # Get the memory manager from the agent loop context
            from vibe.core.agent_loop import AgentLoop
            if hasattr(self._context, 'agent_loop') and isinstance(self._context.agent_loop, AgentLoop):
                self._memory_manager = self._context.agent_loop.memory_manager
            else:
                raise RuntimeError("Memory manager not available in current context")
        return self._memory_manager

    def list_memories(self, args: ListMemoriesArgs) -> ListMemoriesResult:
        """List all available memories in the current session.

        Returns:
            List of memories with their metadata including triggers, titles, and priorities.
        """
        try:
            memories = self.memory_manager.list_memories()

            memory_list = []
            for i, memory in enumerate(memories):
                memory_list.append({
                    "id": i,
                    "title": memory.metadata.title or f"Memory {i}",
                    "triggers": [str(trigger) for trigger in memory.metadata.triggers],
                    "priority": memory.metadata.priority,
                    "content_preview": memory.content[:100] + "..." if len(memory.content) > 100 else memory.content
                })

            return ListMemoriesResult(memories=memory_list)

        except Exception as e:
            return ListMemoriesResult(memories=[], error=str(e))

    def add_memory(self, args: AddMemoryArgs) -> AddMemoryResult:
        """Add a new memory to the current session.

        Args:
            content: The memory content in markdown format
            triggers: List of triggers (e.g., ['always', 'tool_use:grep'])
            title: Optional title for the memory
            priority: Priority for loading (higher = loads first)

        Returns:
            Success status and path to created memory file.
        """
        try:
            file_path = self.memory_manager.create_memory(
                content=args.content,
                triggers=args.triggers,
                title=args.title,
                priority=args.priority
            )
            return AddMemoryResult(
                success=True,
                memory_path=str(file_path),
                error=None
            )
        except Exception as e:
            return AddMemoryResult(
                success=False,
                memory_path=None,
                error=str(e)
            )

    def remove_memory(self, args: RemoveMemoryArgs) -> RemoveMemoryResult:
        """Remove a memory from the current session.

        Args:
            memory_id: Index of the memory to remove (from list_memories)

        Returns:
            Success status and path to removed file.
        """
        try:
            memories = self.memory_manager.list_memories()
            if args.memory_id < 0 or args.memory_id >= len(memories):
                return RemoveMemoryResult(
                    success=False,
                    removed_path=None,
                    error=f"Invalid memory ID: {args.memory_id}"
                )

            # Get the memory file path
            memory_files = list(self.memory_manager.memory_dir.glob("*.md"))
            if not memory_files:
                return RemoveMemoryResult(
                    success=False,
                    removed_path=None,
                    error="No memory files found"
                )

            # For simplicity, remove the file at the same index
            # In a more robust implementation, we'd track file paths per memory
            file_to_remove = memory_files[args.memory_id]
            success = self.memory_manager.delete_memory(file_to_remove)

            return RemoveMemoryResult(
                success=success,
                removed_path=str(file_to_remove) if success else None,
                error=None if success else "Failed to delete memory file"
            )
        except Exception as e:
            return RemoveMemoryResult(
                success=False,
                removed_path=None,
                error=str(e)
            )


def get_tool_class() -> type[MemoryTool]:
    """Get the MemoryTool class for dynamic loading."""
    return MemoryTool