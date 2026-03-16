from __future__ import annotations

from enum import StrEnum, auto
from pathlib import Path

from vibe import VIBE_ROOT

_PROMPTS_DIR = VIBE_ROOT / "core" / "prompts"


class Prompt(StrEnum):
    @property
    def path(self) -> Path:
        return (_PROMPTS_DIR / self.value).with_suffix(".md")

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8", errors="ignore").strip()


class SystemPrompt(Prompt):
    CLI = auto()
    EXPLORE = auto()
    TESTS = auto()
    LEAN = auto()


class UtilityPrompt(Prompt):
    AGENTS_DOC = auto()
    COMPACT = auto()
    DANGEROUS_DIRECTORY = auto()
    PROJECT_CONTEXT = auto()


__all__ = ["SystemPrompt", "UtilityPrompt"]
