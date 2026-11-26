from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
import tomllib
from typing import Any

from vibe.agents import AgentSafety, AgentType
from vibe.core.agents._migration import (
    LEGACY_BASE_DISABLED_KEY,
    migrate_agent_profile_config,
)
from vibe.core.config._defaults import SHELL_TOOLS

# A post_agent hook's `match = "main"` selects the user's agent, whatever its mode,
# so no agent profile may take that name.
RESERVED_AGENT_NAME = "main"


class ApprovalPolicyName(StrEnum):
    ASK = "ask"
    ACCEPT_EDITS = "accept-edits"
    SMART_APPROVE = "smart-approve"
    AUTO_APPROVE = "auto-approve"


class BuiltinAgentName(StrEnum):
    DEFAULT = "default"
    EXPLORE = "explore"
    LEAN = "lean"


@dataclass(frozen=True)
class AgentProfile:
    name: str
    display_name: str
    description: str
    safety: AgentSafety
    agent_type: AgentType = AgentType.AGENT
    overrides: dict[str, Any] = field(default_factory=dict)
    install_required: bool = False
    instructions: str | None = None
    source_path: Path | None = None
    """The file this profile was parsed from, or ``None`` when it is built in.

    The Unified Harness advertises a subagent to the model with the path of the
    file behind it, so a profile that reaches that surface has to remember where
    it came from.
    """
    ignored_settings: tuple[str, ...] = field(default=(), compare=False)
    """The settings an agent file wrote that an agent may not set, dropped."""
    config_error: str | None = field(default=None, compare=False)
    """The config error the profile's settings make, or ``None`` when they are
    valid. A profile with one stays discovered, so choosing it gives this error
    as a broken ``config.toml`` does, rather than "not found".
    """

    @classmethod
    def from_toml(cls, path: Path) -> AgentProfile:
        if path.stem.casefold() == RESERVED_AGENT_NAME:
            raise ValueError(
                f"'{RESERVED_AGENT_NAME}' is reserved for hook matching; "
                "rename the agent file"
            )
        with path.open("rb") as f:
            data = tomllib.load(f)
        migrate_agent_profile_config(data)
        data.pop(LEGACY_BASE_DISABLED_KEY, None)
        name = path.stem
        display_name = data.pop("display_name", name.replace("-", " ").title())
        description = data.pop("description", "")
        instructions = data.pop("instructions", None)
        agent_type = _enum_field(data, "agent_type", AgentType, AgentType.AGENT)
        ignored: tuple[str, ...] = ()
        if agent_type is AgentType.AGENT and name not in BUILTIN_APPROVAL_POLICIES:
            data, ignored = _agent_settings(data)
            safety = AgentSafety.NEUTRAL
        else:
            data.pop("approval_policy", None)
            safety = _enum_field(data, "safety", AgentSafety, AgentSafety.NEUTRAL)
        return cls(
            name=name,
            display_name=display_name,
            description=description,
            safety=safety,
            agent_type=agent_type,
            instructions=instructions,
            overrides=data,
            source_path=path,
            ignored_settings=ignored,
        )


# What an agent may set: what it is, never what runs without asking, which
# belongs to the approval policy, nor org-level lists.
_AGENT_KEYS = frozenset({
    "active_model",
    "models",
    "providers",
    "allowed_models",
    "compaction_model",
    "vision_model",
    "auto_compact_threshold",
    "enabled_tools",
    "disabled_tools",
    "enabled_skills",
    "disabled_skills",
    "skill_paths",
    "system_prompt_id",
    "system_prompt_behavior",
    "tools",
})
_NOT_CONFIG_KEYS = frozenset({"safety", "approval_policy"})
_AGENT_TOOL_KEYS = frozenset({"default_timeout", "max_timeout_seconds"})
_NARROWING_PERMISSIONS = frozenset({"ask", "never"})


def _agent_settings(data: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """The settings of an agent file an agent may set, and the dotted keys of
    the rest. Per tool it keeps the shell timeouts and a ``permission`` that
    narrows (``ask``, ``never``).
    """
    from vibe.core.config.vibe_schema import VibeConfigSchema

    kept: dict[str, Any] = {}
    ignored: list[str] = []
    for key, value in data.items():
        if key not in _AGENT_KEYS and (
            key in VibeConfigSchema.model_fields or key in _NOT_CONFIG_KEYS
        ):
            ignored.append(key)
        elif key not in _AGENT_KEYS:
            # Unknown keys stay, so strict_config refuses a typo when the
            # agent is used, as for any config file.
            kept[key] = value
        elif key == "tools" and isinstance(value, dict):
            tools: dict[str, Any] = {}
            for tool, settings in value.items():
                if not isinstance(settings, dict):
                    ignored.append(f"tools.{tool}")
                    continue
                own: dict[str, Any] = {}
                for setting, setting_value in settings.items():
                    if setting in _AGENT_TOOL_KEYS or (
                        setting == "permission"
                        and setting_value in _NARROWING_PERMISSIONS
                    ):
                        own[setting] = setting_value
                    else:
                        ignored.append(f"tools.{tool}.{setting}")
                if own:
                    tools[tool] = own
            if tools:
                kept["tools"] = tools
        else:
            kept[key] = value
    return kept, tuple(sorted(ignored))


def _enum_field[E: StrEnum](
    data: dict[str, Any], key: str, enum: type[E], default: E
) -> E:
    value = data.pop(key, default)
    try:
        return enum(value)
    except ValueError:
        allowed = ", ".join(repr(member.value) for member in enum)
        raise ValueError(f"{key}: {value!r} is not one of {allowed}") from None


DEFAULT = AgentProfile(
    BuiltinAgentName.DEFAULT,
    "Default",
    "Vibe's own instructions, model and tools",
    AgentSafety.NEUTRAL,
)

ASK = AgentProfile(
    ApprovalPolicyName.ASK,
    "Ask",
    "Requires approval for tool executions",
    AgentSafety.NEUTRAL,
)
ACCEPT_EDITS = AgentProfile(
    ApprovalPolicyName.ACCEPT_EDITS,
    "Accept Edits",
    "Auto-approves file edits only",
    AgentSafety.DESTRUCTIVE,
    overrides={
        "tools": {
            "write_file": {"permission": "always"},
            "edit": {"permission": "always"},
        }
    },
)
AUTO_APPROVE = AgentProfile(
    ApprovalPolicyName.AUTO_APPROVE,
    "Auto Approve",
    "Auto-approves all tool executions",
    AgentSafety.YOLO,
    overrides={"bypass_tool_permissions": True},
)
SMART_APPROVE = AgentProfile(
    ApprovalPolicyName.SMART_APPROVE,
    "Smart Approve",
    "Classifies each tool call and auto-runs the safe ones, prompting only for risky ones",
    AgentSafety.SMART,
    # No static permission overrides: a model classifier gates each call via the
    # runtime "classify" tool mode (Unified Harness only). The runtime derives that
    # gate from the active policy; see build_unified_session_context / _rust_tool_modes.
)

EXPLORE = AgentProfile(
    name=BuiltinAgentName.EXPLORE,
    display_name="Explore",
    description="Read-only subagent for codebase exploration",
    safety=AgentSafety.SAFE,
    agent_type=AgentType.SUBAGENT,
    overrides={
        "enabled_tools": ["grep", "read_file", "skill"],
        "system_prompt_id": "explore",
    },
)

LEAN = AgentProfile(
    name=BuiltinAgentName.LEAN,
    display_name="Lean",
    description="Specialized mode for Lean 4 code analysis, proof assistance, and theorem proving",
    safety=AgentSafety.NEUTRAL,
    agent_type=AgentType.AGENT,
    install_required=True,
    overrides={
        "system_prompt_id": "lean",
        "active_model": "leanstral",
        "allowed_models": ["labs-leanstral-1-5"],
        "providers": [
            {
                "name": "mistral-testing",
                "api_base": "https://api.mistral.ai/v1",
                "api_key_env_var": "MISTRAL_API_KEY",
                "backend": "mistral",
            }
        ],
        "models": [
            {
                "name": "labs-leanstral-1-5",
                "provider": "mistral-testing",
                "alias": "leanstral",
                "thinking": "high",
                "temperature": 1.0,
                "auto_compact_threshold": 200_000,
            }
        ],
        "compaction_model": {
            "name": "mistral-small-latest",
            "provider": "mistral-testing",
            "alias": "devstral-compact",
            "temperature": 0.2,
            "thinking": "off",
        },
        # Every shell tool: the command environment picks one (git_bash or
        # powershell on Windows), whose settings give Core's bash timeouts.
        "tools": {
            name: {"default_timeout": 1200, "max_timeout_seconds": 1200}
            for name in SHELL_TOOLS
        },
    },
)

BUILTIN_AGENTS: dict[str, AgentProfile] = {
    BuiltinAgentName.DEFAULT: DEFAULT,
    BuiltinAgentName.EXPLORE: EXPLORE,
    BuiltinAgentName.LEAN: LEAN,
}

# A file in an agents directory named after one of these overrides the policy,
# not an agent.
BUILTIN_APPROVAL_POLICIES: dict[str, AgentProfile] = {
    ApprovalPolicyName.ASK: ASK,
    ApprovalPolicyName.ACCEPT_EDITS: ACCEPT_EDITS,
    ApprovalPolicyName.SMART_APPROVE: SMART_APPROVE,
    ApprovalPolicyName.AUTO_APPROVE: AUTO_APPROVE,
}

# Builtin modes dropped from the product, mapped to the policy a config that still
# names one as `default_agent` starts on. Plan was read-only: ask runs no tool
# without approval, so the fallback grants no permission the user did not have.
REMOVED_BUILTIN_AGENTS: dict[str, str] = {"plan": ApprovalPolicyName.ASK}
