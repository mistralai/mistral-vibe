from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import ClassVar

from pydantic import BaseModel, Field

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

_MAX_LISTED_FILES = 10


class SkillArgs(BaseModel):
    name: str = Field(description="The name of the skill to load from available_skills")


class SkillResult(BaseModel):
    name: str = Field(description="The name of the loaded skill")
    content: str = Field(description="The full skill content block")
    skill_dir: str | None = Field(
        default=None, description="Absolute path to the skill directory when available"
    )


class SkillToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ALWAYS


class Skill(
    BaseTool[SkillArgs, SkillResult, SkillToolConfig, BaseToolState],
    ToolUIData[SkillArgs, SkillResult],
):
    description: ClassVar[str] = (
        "Load a specialized skill that provides domain-specific instructions and workflows. "
        "When you recognize that a task matches one of the available skills listed in your system prompt, "
        "use this tool to load the full skill instructions. "
        "The skill will inject detailed instructions, workflows, and access to bundled resources "
        "(scripts, references, templates) into the conversation context."
    )

    @classmethod
    def format_call_display(cls, args: SkillArgs) -> ToolCallDisplay:
        return ToolCallDisplay(summary=f"Loading skill: {args.name}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if event.error:
            return ToolResultDisplay(success=False, message=event.error)
        if not isinstance(event.result, SkillResult):
            return ToolResultDisplay(success=True, message="Skill loaded")
        return ToolResultDisplay(
            success=True, message=f"Loaded skill: {event.result.name}"
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

        skill_dir = skill_info.skill_dir
        files: list[str] = []
        if skill_dir is not None:
            try:
                for entry in sorted(skill_dir.rglob("*")):
                    if not entry.is_file():
                        continue
                    if entry.name == "SKILL.md":
                        continue
                    files.append(str(entry.relative_to(skill_dir)))
                    if len(files) >= _MAX_LISTED_FILES:
                        break
            except OSError:
                pass

        file_lines = "\n".join(f"<file>{f}</file>" for f in files)
        base_dir_lines: list[str] = []
        if skill_dir is not None:
            base_dir_lines = [
                f"Base directory for this skill: {skill_dir}",
                "Relative paths in this skill are relative to this base directory.",
            ]

        output = "\n".join([
            f'<skill_content name="{args.name}">',
            f"# Skill: {args.name}",
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
        yield SkillResult(name=args.name, content=output, skill_dir=resolved_skill_dir)
