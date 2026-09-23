from __future__ import annotations

from dataclasses import dataclass

from vibe.core.agents.manager import AgentManager
from vibe.core.agents.models import AgentType, BuiltinAgentName
from vibe.core.config import VibeConfigSchema
from vibe.core.config.orchestrator import ConfigOrchestrator


class AgentInstallError(Exception):
    """A rejected agents/install or agents/uninstall request."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class InstalledAgentsChange:
    previous: list[str]
    """The writable layer's own ``installed_agents`` before the change."""

    next: list[str]
    """The writable layer's own ``installed_agents`` after the change."""

    switch_to: str | None
    """A replacement agent when the active one is being uninstalled."""


def writable_installed_agents(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> list[str] | None:
    """The writable layer's own ``installed_agents``, or None when unavailable.

    ``installed_agents`` concat-merges, so the effective list spans every layer
    while a write can only replace the writable one. Mutations are computed
    against the writable layer's own value, never the merged one, or entries
    contributed by other layers would be copied down on every write.
    """
    try:
        layer = orchestrator.get_layer(orchestrator.writable_layer_name)
    except KeyError:
        return None
    data = layer.cached_data
    names = getattr(data, "installed_agents", None) if data is not None else None
    return list(names) if names else []


def plan_installed_agents_change(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    agents: AgentManager,
    agent_name: str,
    *,
    installed: bool,
) -> InstalledAgentsChange:
    if not agents.is_known_agent(agent_name):
        raise AgentInstallError(f"Agent '{agent_name}' not found.")
    previous = writable_installed_agents(orchestrator)
    if previous is None:
        raise AgentInstallError("The writable config layer is not available.")
    if installed:
        next_names = list(previous)
        if agent_name not in next_names:
            next_names.append(agent_name)
    else:
        next_names = [name for name in previous if name != agent_name]
    switch_to = None
    if not installed and agents.active_profile.name == agent_name:
        switch_to = _replacement_agent(orchestrator.config, agents, agent_name)
    return InstalledAgentsChange(
        previous=previous, next=next_names, switch_to=switch_to
    )


def verify_installed_agents_change(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    agents: AgentManager,
    agent_name: str,
    *,
    installed: bool,
) -> None:
    merged = orchestrator.config.installed_agents
    if installed:
        if agent_name not in merged:
            raise AgentInstallError(f"Agent '{agent_name}' could not be installed.")
        try:
            agents.get_agent(agent_name)
        except ValueError as exc:
            raise AgentInstallError(
                f"Agent '{agent_name}' is installed but not selectable: {exc}"
            ) from exc
    elif agent_name in merged:
        raise AgentInstallError(
            f"Agent '{agent_name}' is installed by a config layer that this"
            " operation cannot edit."
        )


def _replacement_agent(
    config: VibeConfigSchema, agents: AgentManager, excluded: str
) -> str:
    for name in (config.resolve_default_agent(), BuiltinAgentName.ACCEPT_EDITS):
        if name != excluded and _is_selectable(agents, name):
            return name
    for name, profile in agents.available_agents.items():
        if name != excluded and profile.agent_type is AgentType.AGENT:
            return name
    raise AgentInstallError(
        "No selectable agent is available after uninstalling the active agent."
    )


def _is_selectable(agents: AgentManager, name: str) -> bool:
    try:
        return agents.get_agent(name).agent_type is AgentType.AGENT
    except ValueError:
        return False
