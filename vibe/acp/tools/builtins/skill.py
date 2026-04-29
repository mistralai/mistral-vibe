from __future__ import annotations

from pathlib import Path

from acp.helpers import SessionUpdate
from acp.schema import (
    ContentToolCallContent,
    TextContentBlock,
    ToolCallLocation,
    ToolCallProgress,
    ToolCallStart,
)

from vibe import VIBE_ROOT
from vibe.acp.tools.base import (
    ToolCallSessionUpdateProtocol,
    ToolResultSessionUpdateProtocol,
)
from vibe.acp.tools.session_update import (
    failed_tool_result,
    fallback_tool_call,
    resolve_kind,
)
from vibe.core.tools.builtins.skill import (
    Skill as CoreSkillTool,
    SkillArgs,
    SkillResult,
)
from vibe.core.types import ToolCallEvent, ToolResultEvent


class Skill(
    CoreSkillTool, ToolCallSessionUpdateProtocol, ToolResultSessionUpdateProtocol
):
    prompt_path = VIBE_ROOT / "core" / "tools" / "builtins" / "prompts" / "skill.md"

    @classmethod
    def tool_call_session_update(cls, event: ToolCallEvent) -> SessionUpdate | None:
        if not isinstance(event.args, SkillArgs):
            return fallback_tool_call(event, "skill")

        return ToolCallStart(
            session_update="tool_call",
            title=cls.get_call_display(event).summary,
            tool_call_id=event.tool_call_id,
            kind=resolve_kind(event.tool_name),
            raw_input=event.args.model_dump_json(),
            field_meta={"tool_name": event.tool_name, "skill_name": event.args.name},
        )

    @classmethod
    def tool_result_session_update(cls, event: ToolResultEvent) -> SessionUpdate | None:
        if failure := failed_tool_result(event, SkillResult):
            return failure

        result = event.result
        assert isinstance(result, SkillResult)

        locations: list[ToolCallLocation] | None = None
        if result.skill_dir:
            locations = [ToolCallLocation(path=str(Path(result.skill_dir).resolve()))]

        return ToolCallProgress(
            session_update="tool_call_update",
            tool_call_id=event.tool_call_id,
            status="completed",
            content=[
                ContentToolCallContent(
                    type="content",
                    content=TextContentBlock(
                        type="text", text=cls.get_result_display(event).message
                    ),
                )
            ],
            kind=resolve_kind(event.tool_name),
            raw_output=result.model_dump_json(),
            locations=locations,
            field_meta={"tool_name": event.tool_name, "skill_name": result.name},
        )
