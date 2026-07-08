from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from vibe.core.agents._migration import migrate_agent_profile_files
from vibe.core.agents.diagnostics import excluded_agent_message
from vibe.core.agents.models import (
    BUILTIN_AGENTS,
    AgentProfile,
    AgentType,
    BuiltinAgentName,
)
from vibe.core.config.harness_files import get_harness_files_manager
from vibe.core.logger import logger
from vibe.core.paths import dedup_paths
from vibe.core.utils import name_matches

if TYPE_CHECKING:
    from vibe.core.config import AnyVibeConfig


class AgentManager:
    def __init__(
        self,
        config_getter: Callable[[], AnyVibeConfig],
        initial_agent: str = BuiltinAgentName.DEFAULT,
        allow_subagent: bool = False,
    ) -> None:
        self._config_getter = config_getter
        self._search_paths = self._compute_search_paths(self._config)
        self._migrate_agent_profiles()
        self._discovered: dict[str, AgentProfile] = self._discover_agents()

        if custom_names := [n for n in self._discovered if n not in BUILTIN_AGENTS]:
            logger.info(
                "Discovered custom agents %s in %s",
                " ".join(custom_names),
                " ".join(str(p) for p in self._search_paths),
            )

        profile = self.available_agents.get(initial_agent)
        if profile is None:
            if initial_agent in self._discovered:
                raise ValueError(
                    excluded_agent_message(
                        initial_agent, self._config, self._discovered
                    )
                )
            raise ValueError(f"Agent '{initial_agent}' not found.")
        if not allow_subagent and profile.agent_type != AgentType.AGENT:
            raise ValueError(
                f"Agent '{initial_agent}' is a {profile.agent_type} and cannot be used"
                f" as the primary agent. Only agents of type 'agent' can be selected"
                f" with --agent."
            )
        self.active_profile = profile
        self._cached_config: AnyVibeConfig | None = None

    @property
    def _config(self) -> AnyVibeConfig:
        return self._config_getter()

    @property
    def available_agents(self) -> dict[str, AgentProfile]:
        return {
            name: profile
            for name, profile in self._discovered.items()
            if self._is_agent_available(name, profile)
        }

    def _is_agent_available(self, name: str, profile: AgentProfile) -> bool:
        if profile.install_required and name not in self._config.installed_agents:
            return False
        if enabled := self._config.enabled_agents:
            return name_matches(name, enabled)
        return not name_matches(name, self._config.disabled_agents)

    @property
    def config(self) -> AnyVibeConfig:
        if self._cached_config is None:
            self._cached_config = self.active_profile.apply_to_config(self._config)
        return self._cached_config

    def switch_profile(self, name: str) -> None:
        self.active_profile = self.get_agent(name)
        self._cached_config = None

    def preview_config(self, name: str) -> AnyVibeConfig:
        return self.get_agent(name).apply_to_config(self._config)

    def register_agent(self, profile: AgentProfile) -> None:
        self._discovered[profile.name] = profile
        self._cached_config = None

    def invalidate_config(self) -> None:
        self._cached_config = None

    @staticmethod
    def _compute_search_paths(config: AnyVibeConfig) -> list[Path]:
        mgr = get_harness_files_manager()
        return dedup_paths([
            *(p for p in config.agent_paths if p.is_dir()),
            *mgr.project_agents_dirs,
            *mgr.user_agents_dirs,
        ])

    def _discover_agents(self) -> dict[str, AgentProfile]:
        agents: dict[str, AgentProfile] = dict(BUILTIN_AGENTS)

        for base in self._search_paths:
            if not base.is_dir():
                continue
            for agent_file in base.glob("*.toml"):
                if not agent_file.is_file():
                    continue
                if (agent := self._try_load_agent(agent_file)) is not None:
                    if agent.name in BUILTIN_AGENTS:
                        logger.info(
                            "Custom agent '%s' overrides builtin agent", agent.name
                        )
                    elif agent.name in agents:
                        logger.debug(
                            "Skipping duplicate agent '%s' at %s",
                            agent.name,
                            agent_file,
                        )
                        continue
                    agents[agent.name] = agent

        return agents

    def _try_load_agent(self, agent_file: Path) -> AgentProfile | None:
        try:
            agent = AgentProfile.from_toml(agent_file)
            agent.apply_to_config(self._config)
            return agent
        except Exception as e:
            logger.warning("Failed to load agent at %s: %s", agent_file, e)
            return None

    def _migrate_agent_profiles(self) -> None:
        try:
            migrate_agent_profile_files(self._search_paths)
        except Exception as exc:
            logger.warning("Failed to migrate agent profiles", exc_info=exc)

    def get_agent(self, name: str) -> AgentProfile:
        if agent := self.available_agents.get(name):
            return agent
        raise ValueError(f"Agent '{name}' not found")

    def get_subagents(self) -> list[AgentProfile]:
        return [
            a
            for a in self.available_agents.values()
            if a.agent_type == AgentType.SUBAGENT
        ]

    def get_agent_order(self) -> list[str]:
        builtin_order: list[str] = [
            BuiltinAgentName.DEFAULT,
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
