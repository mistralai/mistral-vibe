"""``--auto-approve``/``--yolo`` on a new session, resolved against its agents."""

from __future__ import annotations

from typing import Final

from vibe.app_server.protocol import SessionOptions
from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import AgentProfile, BuiltinAgentName

# Built-in modes that differ from the auto-approve agent only in their approvals.
PERMISSION_MODES: Final = frozenset({
    BuiltinAgentName.ASK,
    BuiltinAgentName.ACCEPT_EDITS,
    BuiltinAgentName.SMART_APPROVE,
})


def resolve_start_auto_approve(
    options: SessionOptions, agents: AgentManager
) -> SessionOptions:
    """The options a new session starts with when it asks for auto-approve.

    On a built-in permission mode the session-wide bypass becomes the
    ``auto-approve`` agent, which approves the same calls but which the user can
    leave. Any other agent, an override of a built-in name included, keeps the
    bypass, so it runs its own prompt and tools with every call approved.
    ``agents`` is the start's, already on the agent the options ask for.
    """
    if not options.auto_approve:
        return options
    active = agents.active_profile
    if options.agent not in {None, active.name}:
        # The requested agent is unavailable and the start fell back.
        return options
    if _bypasses(active):
        return options.model_copy(update={"auto_approve": False})
    target = agents.available_agents.get(BuiltinAgentName.AUTO_APPROVE)
    if (
        active.name not in PERMISSION_MODES
        or active.source_path is not None
        or target is None
        or not _bypasses(target)
    ):
        # A disabled mode clamps the bypass and reports it, as before.
        return options
    return options.model_copy(
        update={"agent": BuiltinAgentName.AUTO_APPROVE.value, "auto_approve": False}
    )


def _bypasses(profile: AgentProfile) -> bool:
    return profile.overrides.get("bypass_tool_permissions") is True
