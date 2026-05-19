from __future__ import annotations

from enum import StrEnum, auto
from pathlib import Path

from vibe import VIBE_ROOT
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.utils.io import read_safe

PROMPTS_DIR = VIBE_ROOT / "core" / "prompts"


class Prompt(StrEnum):
    @property
    def path(self) -> Path:
        return (PROMPTS_DIR / self.value).with_suffix(".md")

    def read(self) -> str:
        return read_safe(self.path).text.strip()


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
    TURN_SUMMARY = auto()


class MissingPromptFileError(RuntimeError):
    def __init__(self, system_prompt_id: str, *prompt_dirs: str) -> None:
        dirs_str = " or ".join(prompt_dirs) if prompt_dirs else "<no prompt dirs>"
        super().__init__(
            f"Invalid system_prompt_id value: '{system_prompt_id}'. "
            f"Must be one of the available prompts ({', '.join(p.name.lower() for p in SystemPrompt)}), "
            f"or correspond to a .md file in {dirs_str}"
        )
        self.system_prompt_id = system_prompt_id


def load_system_prompt(prompt_id: str) -> str:
    mgr = get_harness_files_manager()
    prompt_dirs = mgr.project_prompts_dirs + mgr.user_prompts_dirs
    for current_prompt_dir in prompt_dirs:
        custom_sp_path = (current_prompt_dir / prompt_id).with_suffix(".md")
        if custom_sp_path.is_file():
            return read_safe(custom_sp_path).text

    try:
        return SystemPrompt[prompt_id.upper()].read()
    except KeyError:
        pass

    builtin_path = (PROMPTS_DIR / prompt_id).with_suffix(".md")
    if builtin_path.is_file():
        return read_safe(builtin_path).text.strip()

    raise MissingPromptFileError(
        prompt_id, *(str(d) for d in [*prompt_dirs, PROMPTS_DIR])
    )


__all__ = [
    "PROMPTS_DIR",
    "MissingPromptFileError",
    "SystemPrompt",
    "UtilityPrompt",
    "load_system_prompt",
]
