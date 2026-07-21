from __future__ import annotations

from enum import StrEnum, auto

from pydantic import BaseModel, ConfigDict, Field


class GoalStatus(StrEnum):
    WORKING = auto()
    VERIFYING = auto()
    WAITING = auto()
    BLOCKED = auto()
    PAUSED = auto()
    COMPLETED = auto()


class Goal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal_id: str
    description: str
    verify_command: str | None = None
    repeat: int = 1
    constraints: list[str] = Field(default_factory=list)
    max_iterations: int = 8

    status: GoalStatus = GoalStatus.WORKING
    iteration: int = 1
    last_pass_count: int = 0
    completed: bool = False
    evidence: list[str] = Field(default_factory=list)
