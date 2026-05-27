from __future__ import annotations

from collections.abc import Iterable, Mapping
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
    MINIMAL = auto()


class UtilityPrompt(Prompt):
    AGENTS_DOC = auto()
    COMPACT = auto()
    COMPACT_SUMMARY_PREFIX = auto()
    DANGEROUS_DIRECTORY = auto()
    PROJECT_CONTEXT = auto()
    TURN_SUMMARY = auto()


class MissingPromptFileError(ValueError):
    def __init__(
        self,
        setting_name: str,
        prompt_id: str,
        builtin_ids: Iterable[str],
        custom_dirs: Iterable[Path],
        custom_ids: Iterable[str],
    ) -> None:
        builtin_hint = ", ".join('"' + i + '"' for i in builtin_ids)
        dirs_hint = " or ".join(str(d) for d in custom_dirs) or "<no prompt dirs>"
        custom_hint = ", ".join('"' + i + '"' for i in custom_ids) or "<none>"
        super().__init__(
            f"Invalid {setting_name} value: '{prompt_id}'. "
            f"Must be one of the available prompts ({builtin_hint}), "
            f"or correspond to a .md file in {dirs_hint} (available: {custom_hint})"
        )
        self.setting_name = setting_name
        self.prompt_id = prompt_id


def _validate_prompt_id(prompt_id: str, setting_name: str) -> None:
    if (
        not prompt_id
        or prompt_id in {".", ".."}
        or "/" in prompt_id
        or "\\" in prompt_id
    ):
        raise ValueError(
            f"Invalid {setting_name} value: '{prompt_id}' must be a bare filename "
            "without path separators"
        )


def load_prompt(
    prompt_id: str, *, setting_name: str, builtins: Mapping[str, Path]
) -> str:
    _validate_prompt_id(prompt_id, setting_name)
    mgr = get_harness_files_manager()
    custom_dirs = mgr.project_prompts_dirs + mgr.user_prompts_dirs
    for d in custom_dirs:
        path = (d / prompt_id).with_suffix(".md")
        if path.is_file():
            return read_safe(path).text.strip()

    builtin_path = builtins.get(prompt_id.lower())
    if builtin_path is not None and builtin_path.is_file():
        return read_safe(builtin_path).text.strip()

    custom_ids = sorted({p.stem for d in custom_dirs for p in d.glob("*.md")})
    raise MissingPromptFileError(
        setting_name, prompt_id, tuple(builtins), custom_dirs, custom_ids
    )


def load_system_prompt(prompt_id: str) -> str:
    builtins: dict[str, Path] = {p.name.lower(): p.path for p in SystemPrompt}
    # Experiment variants may reference bundled .md files not in the enum.
    fallback = (PROMPTS_DIR / prompt_id).with_suffix(".md")
    if fallback.is_file():
        builtins.setdefault(prompt_id.lower(), fallback)
    return load_prompt(prompt_id, setting_name="system_prompt_id", builtins=builtins)


__all__ = [
    "PROMPTS_DIR",
    "MissingPromptFileError",
    "Prompt",
    "SystemPrompt",
    "UtilityPrompt",
    "load_prompt",
    "load_system_prompt",
]
