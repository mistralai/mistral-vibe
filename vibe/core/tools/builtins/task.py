from __future__ import annotations

import fnmatch

from pydantic import BaseModel, ConfigDict, Field

from vibe.core.agents.models import BuiltinAgentName
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState, ToolPermission
from vibe.core.tools.permissions import PermissionContext


class TaskArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: str = Field(description="The task for the agent to perform")
    agent: str = Field(
        default="explore",
        description="The type of specialized subagent to use for this task",
    )


class TaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response: str = Field(description="The accumulated response from the subagent")
    turns_used: int = Field(description="Number of turns the subagent used")
    completed: bool = Field(description="Whether the task completed normally")


class TaskToolConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    allowlist: list[str] = Field(default=[BuiltinAgentName.EXPLORE])


class Task(BaseTool[TaskArgs, TaskResult, TaskToolConfig, BaseToolState]):
    def resolve_permission(self, args: TaskArgs) -> PermissionContext | None:
        agent_name = args.agent

        for pattern in self.config.denylist:
            if fnmatch.fnmatch(agent_name, pattern):
                return PermissionContext(permission=ToolPermission.NEVER)

        for pattern in self.config.allowlist:
            if fnmatch.fnmatch(agent_name, pattern):
                return PermissionContext(permission=ToolPermission.ALWAYS)

        return None
