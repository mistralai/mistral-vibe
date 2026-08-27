from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibe.core.agents.manager import AgentManager
    from vibe.core.agents.models import (
        ACCEPT_EDITS,
        ASK,
        AUTO_APPROVE,
        BUILTIN_AGENTS,
        EXPLORE,
        PLAN,
        AgentProfile,
        AgentSafety,
        AgentType,
        BuiltinAgentName,
    )

__all__ = [
    "ACCEPT_EDITS",
    "ASK",
    "AUTO_APPROVE",
    "BUILTIN_AGENTS",
    "EXPLORE",
    "PLAN",
    "AgentManager",
    "AgentProfile",
    "AgentSafety",
    "AgentType",
    "BuiltinAgentName",
]

_MAPPING: dict[str, tuple[str, str]] = {
    "AgentManager": ("vibe.core.agents.manager", "AgentManager"),
    "ACCEPT_EDITS": ("vibe.core.agents.models", "ACCEPT_EDITS"),
    "ASK": ("vibe.core.agents.models", "ASK"),
    "AUTO_APPROVE": ("vibe.core.agents.models", "AUTO_APPROVE"),
    "BUILTIN_AGENTS": ("vibe.core.agents.models", "BUILTIN_AGENTS"),
    "EXPLORE": ("vibe.core.agents.models", "EXPLORE"),
    "PLAN": ("vibe.core.agents.models", "PLAN"),
    "AgentProfile": ("vibe.core.agents.models", "AgentProfile"),
    "AgentSafety": ("vibe.core.agents.models", "AgentSafety"),
    "AgentType": ("vibe.core.agents.models", "AgentType"),
    "BuiltinAgentName": ("vibe.core.agents.models", "BuiltinAgentName"),
}


def __getattr__(name: str) -> object:
    if name in _MAPPING:
        import importlib

        module_name, attr_name = _MAPPING[name]
        module = importlib.import_module(module_name)
        value = getattr(module, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
