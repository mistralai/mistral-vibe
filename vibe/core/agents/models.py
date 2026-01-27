from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto
from pathlib import Path
import tomllib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class AgentSafety(StrEnum):
    SAFE = auto()
    NEUTRAL = auto()
    DESTRUCTIVE = auto()
    YOLO = auto()


class AgentType(StrEnum):
    AGENT = auto()
    SUBAGENT = auto()


class BuiltinAgentName(StrEnum):
    DEFAULT = "default"
    PLAN = "plan"
    ACCEPT_EDITS = "accept-edits"
    AUTO_APPROVE = "auto-approve"
    EXPLORE = "explore"


@dataclass(frozen=True)
class AgentProfile:
    name: str
    display_name: str
    description: str
    safety: AgentSafety
    agent_type: AgentType = AgentType.AGENT
    overrides: dict[str, Any] = field(default_factory=dict)

    def apply_to_config(self, base: VibeConfig) -> VibeConfig:
        from vibe.core.config import VibeConfig as VC

        merged = _deep_merge(base.model_dump(), self.overrides)
        return VC.model_validate(merged)

    @classmethod
    def from_toml(cls, path: Path) -> AgentProfile:
        with path.open("rb") as f:
            data = tomllib.load(f)
        return cls(
            name=path.stem,
            display_name=data.pop("display_name", path.stem.replace("-", " ").title()),
            description=data.pop("description", ""),
            safety=AgentSafety(data.pop("safety", AgentSafety.NEUTRAL)),
            agent_type=AgentType(data.pop("agent_type", AgentType.AGENT)),
            overrides=data,
        )


PLAN_AGENT_TOOLS = ["grep", "read_file", "todo", "ask_user_question", "task"]

DEFAULT = AgentProfile(
    BuiltinAgentName.DEFAULT,
    "Default",
    "Requires approval for tool executions",
    AgentSafety.NEUTRAL,
)
PLAN = AgentProfile(
    BuiltinAgentName.PLAN,
    "Plan",
    "Read-only agent for exploration and planning",
    AgentSafety.SAFE,
    overrides={"auto_approve": True, "enabled_tools": PLAN_AGENT_TOOLS},
)
ACCEPT_EDITS = AgentProfile(
    BuiltinAgentName.ACCEPT_EDITS,
    "Accept Edits",
    "Auto-approves file edits only",
    AgentSafety.DESTRUCTIVE,
    overrides={
        "tools": {
            "write_file": {"permission": "always"},
            "search_replace": {"permission": "always"},
        }
    },
)
AUTO_APPROVE = AgentProfile(
    BuiltinAgentName.AUTO_APPROVE,
    "Auto Approve",
    "Auto-approves all tool executions",
    AgentSafety.YOLO,
    overrides={"auto_approve": True},
)

EXPLORE = AgentProfile(
    name=BuiltinAgentName.EXPLORE,
    display_name="Explore",
    description="Read-only subagent for codebase exploration",
    safety=AgentSafety.SAFE,
    agent_type=AgentType.SUBAGENT,
    overrides={"enabled_tools": ["grep", "read_file"]},
)

BUILTIN_AGENTS: dict[str, AgentProfile] = {
    BuiltinAgentName.DEFAULT: DEFAULT,
    BuiltinAgentName.PLAN: PLAN,
    BuiltinAgentName.ACCEPT_EDITS: ACCEPT_EDITS,
    BuiltinAgentName.AUTO_APPROVE: AUTO_APPROVE,
    BuiltinAgentName.EXPLORE: EXPLORE,
}
