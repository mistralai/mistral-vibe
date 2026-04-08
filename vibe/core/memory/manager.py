from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from vibe.core.paths import VIBE_HOME
from vibe.core.utils.io import read_safe


class Memory(BaseModel):
    """A single memory entry."""

    name: str = Field(description="Short identifier for the memory.")
    type: str = Field(description="Category: feedback, user, project, or reference.")
    description: str = Field(description="One-line summary used to decide relevance.")
    content: str = Field(description="Full memory content.")
    created: str = Field(description="ISO timestamp when memory was created.")
    source: str = Field(
        default="manual", description="How memory was created: manual or auto-learned."
    )


MEMORY_TYPES = {"feedback", "user", "project", "reference"}


class MemoryManager:
    """Manages reading and writing persistent memories."""

    def __init__(
        self, global_dir: Path | None = None, project_dir: Path | None = None
    ) -> None:
        self.global_dir = global_dir or VIBE_HOME.path / "memory"
        self.project_dir = project_dir or Path.cwd() / ".vibe" / "memory"

    def _ensure_dir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def _slugify(self, name: str) -> str:
        """Convert a name to a safe filename."""
        slug = name.lower().replace(" ", "_")
        slug = "".join(c for c in slug if c.isalnum() or c == "_")
        return slug[:80] or "memory"

    def _parse_memory_file(self, path: Path) -> Memory | None:
        """Parse a markdown memory file with YAML-like frontmatter."""
        try:
            text = read_safe(path)
        except OSError:
            return None

        if not text.startswith("---"):
            return None

        parts = text.split("---", 2)
        _min_parts = 3
        if len(parts) < _min_parts:
            return None

        frontmatter_text = parts[1].strip()
        content = parts[2].strip()

        # Simple YAML-like parsing (key: value per line)
        meta: dict[str, str] = {}
        for line in frontmatter_text.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip()

        if not meta.get("name") or not meta.get("type"):
            return None

        return Memory(
            name=meta.get("name", ""),
            type=meta.get("type", ""),
            description=meta.get("description", ""),
            content=content,
            created=meta.get("created", ""),
            source=meta.get("source", "manual"),
        )

    def list_memories(self, scope: str = "all") -> list[Memory]:
        """List all memories from global and/or project directories."""
        memories: list[Memory] = []

        dirs: list[Path] = []
        if scope in {"all", "global"}:
            dirs.append(self.global_dir)
        if scope in {"all", "project"}:
            dirs.append(self.project_dir)

        for directory in dirs:
            if not directory.is_dir():
                continue
            for file in sorted(directory.glob("*.md")):
                if mem := self._parse_memory_file(file):
                    memories.append(mem)

        return memories

    def read_memory(self, name: str) -> Memory | None:
        """Read a specific memory by name."""
        slug = self._slugify(name)
        for directory in [self.project_dir, self.global_dir]:
            path = directory / f"{slug}.md"
            if path.is_file():
                return self._parse_memory_file(path)
        return None

    def write_memory(
        self,
        name: str,
        type: str,
        description: str,
        content: str,
        scope: str = "project",
        source: str = "manual",
    ) -> Path:
        """Write a memory to disk."""
        if type not in MEMORY_TYPES:
            msg = f"Invalid memory type: {type}. Must be one of {MEMORY_TYPES}"
            raise ValueError(msg)

        directory = self.project_dir if scope == "project" else self.global_dir
        self._ensure_dir(directory)

        slug = self._slugify(name)
        path = directory / f"{slug}.md"
        created = datetime.now(UTC).isoformat()

        file_content = f"""---
name: {name}
type: {type}
description: {description}
created: {created}
source: {source}
---

{content}
"""
        path.write_text(file_content, encoding="utf-8")
        return path

    def delete_memory(self, name: str) -> bool:
        """Delete a memory by name."""
        slug = self._slugify(name)
        for directory in [self.project_dir, self.global_dir]:
            path = directory / f"{slug}.md"
            if path.is_file():
                path.unlink()
                return True
        return False

    def get_context_string(self) -> str:
        """Get all memories formatted for inclusion in system prompt."""
        memories = self.list_memories()
        if not memories:
            return ""

        lines = [
            "# Memories",
            "",
            "The following memories were saved from previous sessions:",
            "",
        ]
        for mem in memories:
            lines.append(f"## [{mem.type}] {mem.name}")
            lines.append(f"*{mem.description}*")
            lines.append("")
            lines.append(mem.content)
            lines.append("")

        return "\n".join(lines)
