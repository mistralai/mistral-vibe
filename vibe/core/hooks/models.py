from __future__ import annotations

from enum import auto
from pathlib import Path

from pydantic import BaseModel, field_validator

from vibe.core.types import BaseEvent, StrEnum

# --- Types & enums ---


class HookMessageSeverity(StrEnum):
    OK = auto()
    WARNING = auto()
    ERROR = auto()


class HookType(StrEnum):
    POST_AGENT_TURN = auto()


# --- Declarative hook config (TOML on disk) ---


class HookConfig(BaseModel):
    name: str
    type: HookType
    command: str
    timeout: float = 30.0
    description: str | None = None

    @field_validator("command")
    @classmethod
    def command_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("command must not be empty")
        return v


class HookConfigIssue(BaseModel):
    file: Path
    message: str


class HookConfigResult(BaseModel):
    hooks: list[HookConfig]
    issues: list[HookConfigIssue]


# --- Subprocess execution ---


class HookInvocation(BaseModel):
    session_id: str
    transcript_path: str
    cwd: str
    hook_event_name: str


class HookExecutionResult(BaseModel):
    hook_name: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool


# --- Injected user message (retry / hook stdout) ---


class HookUserMessage(BaseModel):
    content: str


# --- Transcript / UI events (BaseEvent) ---


class HookEvent(BaseEvent):
    pass


class HookRunStartEvent(HookEvent):
    pass


class HookRunEndEvent(HookEvent):
    pass


class HookStartEvent(HookEvent):
    hook_name: str


class HookEndEvent(HookEvent):
    hook_name: str
    status: HookMessageSeverity
    content: str | None = None
