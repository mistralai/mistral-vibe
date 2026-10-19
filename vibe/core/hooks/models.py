from __future__ import annotations

from enum import auto
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from vibe.core.types import BaseEvent, StrEnum

# --- Types & enums ---


class HookMessageSeverity(StrEnum):
    OK = auto()
    WARNING = auto()
    ERROR = auto()


class HookType(StrEnum):
    POST_AGENT = auto()
    PRE_TOOL = auto()
    POST_TOOL = auto()


class HookSource(StrEnum):
    HOST = auto()
    PROJECT = auto()
    PROJECT_PLUGIN = auto()
    GLOBAL = auto()
    GLOBAL_PLUGIN = auto()
    CLIENT = auto()


class HookVisibility(StrEnum):
    PUBLIC = auto()
    PRIVATE = auto()


class HookProtocol(StrEnum):
    VIBE = auto()
    CLAUDE_CODE = auto()
    KIMI_CODE = auto()


_DEFAULT_HOOK_TIMEOUT = 60.0


# --- Declarative hook config (TOML on disk) ---


class HookConfig(BaseModel):
    name: str
    type: HookType
    command: str
    match: str | None = None
    timeout: float | None = None
    strict: bool = False
    description: str | None = None

    @field_validator("command")
    @classmethod
    def command_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("command must not be empty")
        return v

    @field_validator("match")
    @classmethod
    def match_not_blank(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("match must not be empty")
        return v

    @model_validator(mode="after")
    def _apply_defaults_and_constraints(self) -> Self:
        if self.match is not None and self.type == HookType.POST_AGENT:
            raise ValueError(
                "match is only valid for tool hooks (pre_tool / post_tool)"
            )
        if self.strict and self.type == HookType.POST_AGENT:
            raise ValueError(
                "strict is only valid for tool hooks (pre_tool / post_tool)"
            )
        if self.timeout is None:
            self.timeout = _DEFAULT_HOOK_TIMEOUT
        return self


class RuntimeHookDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    config: HookConfig
    source: HookSource
    order: int = 0
    cwd: Path | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    visibility: HookVisibility = HookVisibility.PUBLIC
    protocol: HookProtocol = HookProtocol.VIBE
    plugin_name: str | None = None
    declared_name: str | None = None
    config_file: Path | None = None

    @model_validator(mode="after")
    def validate_plugin_source(self) -> Self:
        is_plugin = self.source in {HookSource.PROJECT_PLUGIN, HookSource.GLOBAL_PLUGIN}
        if is_plugin and self.plugin_name is None:
            raise ValueError("plugin hook definitions require plugin_name")
        if not is_plugin and self.plugin_name is not None:
            raise ValueError("non-plugin hook definitions cannot set plugin_name")
        return self


class HookConfigIssue(BaseModel):
    file: Path
    message: str


class HookConfigResult(BaseModel):
    hooks: list[HookConfig]
    issues: list[HookConfigIssue]
    runtime_hooks: list[RuntimeHookDefinition] = Field(default_factory=list)


# --- Subprocess execution ---


# --- Structured stdout response (exit 0 + JSON) ---


# --- Decision values (consumed by the agent loop) ---


# --- Transcript / UI events (BaseEvent) ---


class HookEvent(BaseEvent):
    pass


class HookRunStartEvent(HookEvent):
    scope: HookType = HookType.POST_AGENT
    tool_name: str | None = None
    tool_call_id: str | None = None


class HookRunEndEvent(HookEvent):
    scope: HookType = HookType.POST_AGENT
    tool_call_id: str | None = None


# scope / tool_call_id let consumers route events when concurrent tool-call
# chains interleave on the wire.
class HookStartEvent(HookEvent):
    hook_name: str
    scope: HookType = HookType.POST_AGENT
    tool_call_id: str | None = None


class HookEndEvent(HookEvent):
    hook_name: str
    status: HookMessageSeverity
    content: str | None = None
    scope: HookType = HookType.POST_AGENT
    tool_call_id: str | None = None
