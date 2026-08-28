from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import os
from pathlib import Path

from pydantic import BaseModel, Field

from vibe.core.skills.models import SkillInfo
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.permissions import PermissionContext
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import ToolResultEvent, ToolStreamEvent
from vibe.utils.tool_presentation import ToolEffectKind

_MAX_LISTED_FILES = 10
_MAX_WALKED_ENTRIES = 200
_SKIP_DIR_NAMES = frozenset({
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
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
            for name in filenames:
                if name == "SKILL.md":
                    continue
                files.append(str(Path(root, name).relative_to(skill_dir)))
            if len(files) >= _MAX_WALKED_ENTRIES:
                break
    except OSError:
        pass
    return sorted(files)[:_MAX_LISTED_FILES]


def render_skill_result(skill_info: SkillInfo, files: list[str]) -> SkillResult:
    skill_dir = skill_info.skill_dir

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


def already_loaded_result(skill_info: SkillInfo) -> SkillResult:
    skill_dir = skill_info.skill_dir
    return SkillResult(
        name=skill_info.name,
        content=already_loaded_message(skill_info.name),
        skill_dir=None if skill_dir is None else str(skill_dir),
    )


async def build_skill_result(
    skill_info: SkillInfo, *, already_loaded: bool
) -> SkillResult:
    if already_loaded:
        return already_loaded_result(skill_info)
    files = await asyncio.to_thread(sample_skill_files, skill_info.skill_dir)
    return render_skill_result(skill_info, files)


class SkillToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class Skill(
    BaseTool[SkillArgs, SkillResult, SkillToolConfig, BaseToolState],
    ToolUIData[SkillArgs, SkillResult],
):
    effect_kind = ToolEffectKind.SKILL

    @classmethod
    def format_call_display(cls, args: SkillArgs) -> ToolCallDisplay:
        return ToolCallDisplay(
            summary=f"Loading skill: {args.name}",
            verb="Loading",
            message=f"skill: {args.name}",
            settled_verb="Loaded",
            settled_message=f"skill: {args.name}",
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if event.error:
            return ToolResultDisplay(success=False, message=event.error)
        if not isinstance(event.result, SkillResult):
            return ToolResultDisplay(success=True, message="Skill loaded")
        return ToolResultDisplay(
            success=True, verb="Loaded", message=f"skill: {event.result.name}"
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Loading skill"

    def resolve_permission(self, args: SkillArgs) -> PermissionContext | None:
        return PermissionContext(permission=ToolPermission.ALWAYS)

    async def run(
        self, args: SkillArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | SkillResult, None]:
        if ctx is None or ctx.skill_manager is None:
            raise ToolError("Skill manager not available")

        skill_manager = ctx.skill_manager
        skill_info = skill_manager.get_skill(args.name)

        if skill_info is None:
            available = ", ".join(sorted(skill_manager.available_skills.keys()))
            raise ToolError(
                f'Skill "{args.name}" not found. Available skills: {available or "none"}'
            )

        already_loaded = ctx.is_skill_loaded is not None and ctx.is_skill_loaded(
            args.name
        )
        yield await build_skill_result(skill_info, already_loaded=already_loaded)
