from __future__ import annotations

from typing import TYPE_CHECKING

from vibe.core.utils import name_matches

if TYPE_CHECKING:
    from vibe.core.agents.models import AgentProfile
    from vibe.core.config import VibeConfig


def excluded_agent_message(
    name: str, config: VibeConfig, discovered: dict[str, AgentProfile]
) -> str:
    """Generate a message explaining why an agent is not available based on the config."""
    profile = discovered.get(name)
    if (
        profile is not None
        and profile.install_required
        and name not in config.installed_agents
    ):
        return (
            f"Agent '{name}' requires installation. Run it once via --agent "
            f"'{name}', or add it to 'installed_agents'."
        )
    is_default = name == config.default_agent
    label = "default_agent" if is_default else "Agent"
    fix = (
        "set 'default_agent' to an enabled agent"
        if is_default
        else "select an enabled agent"
    )
    if enabled := config.enabled_agents:
        if not name_matches(name, enabled):
            return (
                f"{label} '{name}' is not in 'enabled_agents' {enabled}. "
                f"Add '{name}' to 'enabled_agents', or {fix}."
            )
    elif name_matches(name, config.disabled_agents):
        return (
            f"{label} '{name}' is in 'disabled_agents' "
            f"{config.disabled_agents}. Remove '{name}' from "
            f"'disabled_agents', or {fix}."
        )
    return (
        f"Agent '{name}' is not available. "
        f"It may be disabled, not installed, or excluded by your config."
    )
