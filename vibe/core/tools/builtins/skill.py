from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from vibe.core.skills.models import SkillInfo
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState, ToolPermission
from vibe.core.tools.permissions import PermissionContext

SAMPLED_SKILL_FILES = 10
MAX_WALKED_SKILL_ENTRIES = 200
SKIPPED_SKILL_DIR_NAMES = frozenset({
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "dist",
    "build",
})


def skill_content_marker(name: str) -> str:
    return f'<skill_content name="{name}">'


class SkillArgs(BaseModel):
    name: str = Field(description="The name of the skill from available_skills")


class SkillResult(BaseModel):
    name: str = Field(description="The name of the loaded skill")
    content: str = Field(description="The full skill content block")
    skill_dir: str | None = Field(
        default=None, description="Absolute path to the skill directory when available"
    )


def sample_skill_files(skill_dir: Path | None) -> list[str]:
    if skill_dir is None or not (skill_dir / "SKILL.md").is_file():
        return []
    files: list[str] = []
    try:
        for root, dirnames, filenames in os.walk(skill_dir, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in SKIPPED_SKILL_DIR_NAMES]
            for name in filenames:
                if name == "SKILL.md":
                    continue
                files.append(str(Path(root, name).relative_to(skill_dir)))
            if len(files) >= MAX_WALKED_SKILL_ENTRIES:
                break
    except OSError:
        pass
    return sorted(files)[:SAMPLED_SKILL_FILES]


def render_skill_result(
    skill_info: SkillInfo, files: list[str], *, base_dir: str | None = None
) -> SkillResult:
    """Render a skill's content block.

    ``base_dir`` overrides the skill's own directory, for a skill whose files
    the model reads somewhere else, such as a copy in a sandbox.
    """
    skill_dir = base_dir or skill_info.skill_dir

    file_lines = "\n".join(f"<file>{f}</file>" for f in files)
    base_dir_lines: list[str] = []
    if skill_dir is not None:
        base_dir_lines = [
            f"Base directory for this skill: {skill_dir}",
            "Relative paths in this skill are relative to this base directory.",
        ]

    output = "\n".join([
        skill_content_marker(skill_info.name),
        f"# Skill: {skill_info.name}",
        "",
        skill_info.prompt.strip(),
        "",
        *base_dir_lines,
        "Note: file list is sampled.",
        "",
        "<skill_files>",
        file_lines,
        "</skill_files>",
        "</skill_content>",
    ])

    resolved_skill_dir = None if skill_dir is None else str(skill_dir)
    return SkillResult(
        name=skill_info.name, content=output, skill_dir=resolved_skill_dir
    )


def already_loaded_message(name: str) -> str:
    return (
        f"Skill '{name}' is already loaded earlier in this "
        "conversation. Reuse those instructions."
    )


class SkillToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class Skill(BaseTool[SkillArgs, SkillResult, SkillToolConfig, BaseToolState]):
    def resolve_permission(self, args: SkillArgs) -> PermissionContext | None:
        return PermissionContext(permission=ToolPermission.ALWAYS)
