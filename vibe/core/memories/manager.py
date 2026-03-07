from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, TypedDict
import yaml
from pydantic import BaseModel, Field

from vibe.core.logger import logger
from vibe.core.types import LLMMessage, Role
from vibe.core.paths.global_paths import VIBE_HOME


class MemoryTrigger(BaseModel):
    """Represents a trigger condition for loading a memory."""
    type: str = Field(..., description="Type of trigger (always, tool_use)")
    tool_name: Optional[str] = Field(None, description="Tool name for tool_use triggers")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryTrigger:
        """Create a MemoryTrigger from a dictionary."""
        if ":" in data:
            # Handle "tool_use: tool_name" format
            trigger_type, *rest = data.split(":", 1)
            tool_name = rest[0].strip() if rest else None
            return cls(type=trigger_type, tool_name=tool_name)
        return cls(type=data)

    def matches(self, trigger_type: str, tool_name: Optional[str] = None) -> bool:
        """Check if this trigger matches the given conditions."""
        if self.type != trigger_type:
            return False
        if trigger_type == "tool_use" and self.tool_name != tool_name:
            return False
        return True


class MemoryMetadata(BaseModel):
    """Metadata for a memory entry."""
    triggers: List[MemoryTrigger] = Field(default_factory=list)
    title: Optional[str] = None
    priority: int = Field(default=0, description="Priority for memory loading (higher = load first)")


class MemoryContent(BaseModel):
    """Content of a memory entry."""
    metadata: MemoryMetadata
    content: str


class MemoryManager:
    """Manages storage and retrieval of memories."""

    MEMORY_DIR_NAME = "memories"
    MEMORY_FILE_EXTENSION = ".md"

    def __init__(self, session_dir: Path, cwd: Path | None = None) -> None:
        self.session_dir = session_dir
        self.cwd = cwd or Path.cwd()

        # Memory sources in priority order (highest to lowest)
        self.memory_sources = [
            self.session_dir / self.MEMORY_DIR_NAME,  # Session-specific (highest priority)
            self.cwd / ".vibe" / self.MEMORY_DIR_NAME,  # Project-specific
            VIBE_HOME.path / self.MEMORY_DIR_NAME,  # Global memories (lowest priority)
        ]

        # Ensure all memory directories exist
        for source_dir in self.memory_sources:
            source_dir.parent.mkdir(parents=True, exist_ok=True)
            source_dir.mkdir(exist_ok=True)

    def _ensure_memory_dir_exists(self) -> None:
        """Ensure the memories directory exists."""
        self.memory_dir.mkdir(exist_ok=True)

    def _parse_memory_file(self, file_path: Path) -> MemoryContent | None:
        """Parse a memory file with YAML front matter."""
        try:
            content = file_path.read_text(encoding="utf-8")

            # Split YAML front matter from markdown content
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    yaml_content = parts[1]
                    markdown_content = parts[2].lstrip()

                    # Parse YAML metadata
                    metadata_dict = yaml.safe_load(yaml_content) or {}

                    # Convert triggers
                    triggers_data = metadata_dict.get("triggers", [])
                    triggers = [MemoryTrigger.from_dict(t) for t in triggers_data]

                    metadata = MemoryMetadata(
                        triggers=triggers,
                        title=metadata_dict.get("title"),
                        priority=metadata_dict.get("priority", 0)
                    )

                    return MemoryContent(metadata=metadata, content=markdown_content)

            # Fallback for plain markdown files (always trigger)
            return MemoryContent(
                metadata=MemoryMetadata(triggers=[MemoryTrigger(type="always")]),
                content=content
            )
        except Exception as e:
            logger.warning(f"Failed to parse memory file {file_path}: {e}")
            return None

    def list_memories(self) -> List[MemoryContent]:
        """List all available memories."""


        memories = []
        # Collect memories from all sources (session first, then project, then global)
        for source_dir in self.memory_sources:
            for file_path in source_dir.glob(f"*{self.MEMORY_FILE_EXTENSION}"):
                if memory := self._parse_memory_file(file_path):
                    memories.append(memory)

        # Sort by priority (highest first)
        return sorted(memories, key=lambda m: m.metadata.priority, reverse=True)

    def get_memories_for_trigger(self, trigger_type: str, tool_name: Optional[str] = None) -> List[MemoryContent]:
        """Get memories that match a specific trigger."""
        all_memories = self.list_memories()
        return [
            memory for memory in all_memories
            if any(trigger.matches(trigger_type, tool_name) for trigger in memory.metadata.triggers)
        ]

    def create_memory(self, content: str, triggers: List[str], title: Optional[str] = None,
                      priority: int = 0, scope: str = "session") -> Path:
        """Create a new memory entry.

        Args:
            content: Memory content in markdown
            triggers: List of triggers
            title: Optional title
            priority: Loading priority (higher = loads first)
            scope: Where to save the memory ('session', 'project', or 'global')
        """

        # Convert trigger strings to MemoryTrigger objects
        trigger_objects = [MemoryTrigger.from_dict(t) for t in triggers]

        # Create metadata
        metadata = MemoryMetadata(
            triggers=trigger_objects,
            title=title,
            priority=priority
        )

        # Generate filename (use title or hash)
        if title:
            safe_title = "".join(c if c.isalnum() or c in "_- " else "_" for c in title)
            filename = f"{safe_title[:50]}{self.MEMORY_FILE_EXTENSION}"
        else:
            import hashlib
            content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
            filename = f"memory_{content_hash}{self.MEMORY_FILE_EXTENSION}"

        # Select memory directory based on scope
        if scope == "session":
            memory_dir = self.memory_sources[0]  # Session directory
        elif scope == "project":
            memory_dir = self.memory_sources[1]  # Project directory
        elif scope == "global":
            memory_dir = self.memory_sources[2]  # Global directory
        else:
            raise ValueError(f"Invalid scope: {scope}. Must be 'session', 'project', or 'global'")

        file_path = memory_dir / filename

        # Write file with YAML front matter
        yaml_content = yaml.dump({
            "triggers": triggers,
            "title": title,
            "priority": priority
        })

        file_content = f"---\n{yaml_content}---\n\n{content}"
        file_path.write_text(file_content, encoding="utf-8")

        return file_path

    def delete_memory(self, memory_path: Path) -> bool:
        """Delete a memory entry."""
        if memory_path.exists() and memory_path.parent == self.memory_dir:
            try:
                memory_path.unlink()
                return True
            except OSError as e:
                logger.warning(f"Failed to delete memory {memory_path}: {e}")
        return False

    def convert_to_llm_messages(self, memories: List[MemoryContent]) -> List[LLMMessage]:
        """Convert memories to LLM messages for context injection."""
        messages = []
        for memory in memories:
            # Format memory content with title if available
            if memory.metadata.title:
                content = f"# {memory.metadata.title}\n\n{memory.content}"
            else:
                content = memory.content

            messages.append(LLMMessage(
                role=Role.system,
                content=content,
                name="memory"
            ))
        return messages