from __future__ import annotations

from enum import auto
from pathlib import Path

from vibe import VIBE_ROOT
from vibe.core.compatibility import StrEnum

_PROMPTS_DIR = VIBE_ROOT / "core" / "prompts"


class Prompt(StrEnum):
    @property
    def path(self) -> Path:
        return (_PROMPTS_DIR / self.value).with_suffix(".md")

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8").strip()


class SystemPrompt(Prompt):
    CLI = "cli"
    TESTS = "tests"
    STRICT_JUGGERNAUT = "strict_juggernaut"


class UtilityPrompt(Prompt):
    COMPACT = "compact"
    DANGEROUS_DIRECTORY = "dangerous_directory"
    PROJECT_CONTEXT = "project_context"


__all__ = ["SystemPrompt", "UtilityPrompt"]
