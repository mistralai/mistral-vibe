from __future__ import annotations

from typing import Annotated, Any, Literal, final

from pydantic import BaseModel, ConfigDict, Field, RootModel

from vibe.app_server._unified_scheduled_loops import (
    ScheduledPrompt,
    UnifiedScheduledLoops,
)
from vibe.core.loop import MIN_INTERVAL_SECONDS

CRON_TOOL_NAME = "cron"
CRON_TOOL_DESCRIPTION = """Schedule recurring prompts at an interval (integer seconds, minimum 30) or a \
standard five-field cron expression in the local timezone. List, cancel by ID, \
or clear schedules. At most 50 schedules per session, persisted across resumes. \
Prompts run only while the session is live and idle; missed runs are not caught up."""


@final
class ScheduleArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["schedule"]
    interval_seconds: int = Field(ge=MIN_INTERVAL_SECONDS, strict=True)
    prompt: str = Field(min_length=1)


@final
class ScheduleCronArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["schedule_cron"]
    cron: str = Field(
        min_length=1,
        description="Standard five-field cron expression: minute hour day month weekday",
    )
    prompt: str = Field(min_length=1)


@final
class ListArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["list"]


@final
class CancelArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["cancel"]
    id: str = Field(min_length=1)


@final
class ClearArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["clear"]


class CronArgs(
    RootModel[
        Annotated[
            ScheduleArgs | ScheduleCronArgs | ListArgs | CancelArgs | ClearArgs,
            Field(discriminator="action"),
        ]
    ]
):
    pass


def cron_input_schema() -> dict[str, Any]:
    properties: dict[str, Any] = {}
    actions: list[str] = []
    for args_type in (ScheduleArgs, ScheduleCronArgs, ListArgs, CancelArgs, ClearArgs):
        variant_properties = args_type.model_json_schema()["properties"]
        actions.append(variant_properties["action"]["const"])
        properties.update(
            (name, value)
            for name, value in variant_properties.items()
            if name != "action"
        )

    return {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": actions,
                "description": (
                    "schedule requires interval_seconds and prompt; schedule_cron "
                    "requires cron and prompt; cancel requires id."
                ),
            },
            **properties,
        },
        "required": ["action"],
        "additionalProperties": False,
    }


class CronResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verb: str
    loops: list[ScheduledPrompt] = Field(default_factory=list)
    message: str
    cleared_count: int | None = Field(
        default=None, ge=0, exclude_if=lambda value: value is None
    )


async def run_cron(
    args: CronArgs, *, scheduled_loops: UnifiedScheduledLoops
) -> CronResult:
    action = args.root
    match action.action:
        case "schedule":
            loop = await scheduled_loops.create_interval(
                action.interval_seconds, action.prompt
            )
            return CronResult(
                verb="Scheduled", loops=[loop], message=f"Scheduled loop {loop.id}"
            )
        case "schedule_cron":
            loop = await scheduled_loops.create_cron(action.cron, action.prompt)
            return CronResult(
                verb="Scheduled", loops=[loop], message=f"Scheduled loop {loop.id}"
            )
        case "list":
            loops = await scheduled_loops.list()
            return CronResult(
                verb="Listed",
                loops=loops,
                message=f"Listed {len(loops)} scheduled loops",
            )
        case "cancel":
            loop = await scheduled_loops.delete(action.id)
            return CronResult(
                verb="Cancelled", loops=[loop], message=f"Cancelled loop {loop.id}"
            )
        case "clear":
            count = await scheduled_loops.clear()
            return CronResult(
                verb="Cleared",
                message=f"Cleared {count} scheduled loops",
                cleared_count=count,
            )


__all__ = [
    "CRON_TOOL_DESCRIPTION",
    "CRON_TOOL_NAME",
    "CronArgs",
    "CronResult",
    "cron_input_schema",
    "run_cron",
]
