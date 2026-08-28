from __future__ import annotations

from typing import TYPE_CHECKING

from vibe.core.agents.diagnostics import excluded_agent_message
from vibe.core.agents.models import (
    BUILTIN_AGENTS,
    AgentProfile,
    AgentType,
    BuiltinAgentName,
)
from vibe.core.agents.registry import AgentRegistry, apply_profile_overrides
from vibe.core.config.harness_files import (
    HarnessFilesManager,
    get_harness_files_manager,
)
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.utils import name_matches
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema


class AgentManager:
    def __init__(
        self,
        orchestrator: ConfigOrchestrator[VibeConfigSchema],
        initial_agent: str = BuiltinAgentName.ACCEPT_EDITS,
        allow_subagent: bool = False,
        harness_files: HarnessFilesManager | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._registry = AgentRegistry(
            orchestrator, harness_files or get_harness_files_manager()
        )

        if custom_names := [n for n in self._discovered if n not in BUILTIN_AGENTS]:
            logger.info(
                "Discovered custom agents %s in %s",
                " ".join(custom_names),
                " ".join(str(p) for p in self._registry.search_paths),
            )

        profile = self.available_agents.get(initial_agent)
        if profile is None:
            if initial_agent in self._discovered:
                raise ValueError(
                    excluded_agent_message(initial_agent, self.config, self._discovered)
                )
            raise ValueError(f"Agent '{initial_agent}' not found.")
        if not allow_subagent and profile.agent_type != AgentType.AGENT:
            raise ValueError(
                f"Agent '{initial_agent}' is a {profile.agent_type} and cannot be used"
                f" as the primary agent. Only agents of type 'agent' can be selected"
                f" with --agent."
            )
        self.active_profile = profile
        self._install_profile(profile)

    def rebind(self, harness_files: HarnessFilesManager) -> None:
        """Rediscover agents from a different working directory.

        The active profile is left installed. It was resolved before the move
        and stays valid whether or not the destination also defines it.
        """
        self._registry.rediscover(harness_files)

    @property
    def _discovered(self) -> dict[str, AgentProfile]:
        return self._registry.discovered

    @property
    def config(self) -> VibeConfigSchema:
        return self._orchestrator.config

    @property
    def available_agents(self) -> dict[str, AgentProfile]:
        return {
            name: profile
            for name, profile in self._discovered.items()
            if self._is_agent_available(name, profile)
        }

    def _is_agent_available(self, name: str, profile: AgentProfile) -> bool:
        if profile.install_required and name not in self.config.installed_agents:
            return False
        if enabled := self.config.enabled_agents:
            return name_matches(name, enabled)
        return not name_matches(name, self.config.disabled_agents)

    def switch_profile(self, name: str) -> None:
        self.active_profile = self.get_agent(name)
        self._install_profile(self.active_profile)

    def preview_config(self, name: str) -> VibeConfigSchema:
        candidate = self._orchestrator.copy()
        apply_profile_overrides(candidate, self.get_agent(name).overrides)
        return candidate.config

    def _install_profile(self, profile: AgentProfile) -> None:
        apply_profile_overrides(self._orchestrator, profile.overrides)

    def get_agent(self, name: str) -> AgentProfile:
        if agent := self.available_agents.get(name):
            return agent
        if name in self._discovered:
            raise ValueError(
                excluded_agent_message(name, self.config, self._discovered)
            )
        raise ValueError(f"Agent '{name}' not found")

    def get_subagents(self) -> list[AgentProfile]:
        return [
            a
            for a in self.available_agents.values()
            if a.agent_type == AgentType.SUBAGENT
        ]

    def get_agent_order(self) -> list[str]:
        builtin_order: list[str] = [
            BuiltinAgentName.ASK,
            BuiltinAgentName.PLAN,
            BuiltinAgentName.ACCEPT_EDITS,
            BuiltinAgentName.AUTO_APPROVE,
        ]
        primary_agents = [
            name
            for name, agent in self.available_agents.items()
            if agent.agent_type == AgentType.AGENT
        ]
        order = [name for name in builtin_order if name in primary_agents]
        custom = sorted(name for name in primary_agents if name not in builtin_order)
        return order + custom

    def next_agent(self, current: AgentProfile) -> AgentProfile:
        order = self.get_agent_order()
        idx = order.index(current.name) if current.name in order else -1
        return self.available_agents[order[(idx + 1) % len(order)]]
