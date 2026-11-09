"""Claude's per-user storage location."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ClaudeHome:
    path: Path

    @classmethod
    def for_current_user(cls) -> ClaudeHome:
        return cls(Path.home())

    @property
    def configuration_path(self) -> Path:
        return self.path / ".claude.json"

    @property
    def projects_path(self) -> Path:
        return self.path / ".claude" / "projects"
