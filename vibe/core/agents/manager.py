from __future__ import annotations

from collections.abc import Callable
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING

from vibe.core.agents.models import (
    BUILTIN_AGENTS,
    AgentProfile,
    AgentType,
    BuiltinAgentName,
)
from vibe.core.paths.config_paths import resolve_local_agents_dir
from vibe.core.paths.global_paths import GLOBAL_AGENTS_DIR
from vibe.core.utils import name_matches

if TYPE_CHECKING:
    from vibe.core.config import VibeConfig

logger = getLogger("vibe")


class AgentManager:
    def __init__(
        self,
        config_getter: Callable[[], VibeConfig],
        initial_agent: str = BuiltinAgentName.DEFAULT,
    ) -> None:
        self._config_getter = config_getter
        self._search_paths = self._compute_search_paths(self._config)
        self._available: dict[str, AgentProfile] = self._discover_agents()

        custom_count = len(self._available) - len(BUILTIN_AGENTS)
        if custom_count > 0:
            custom_names = [
                name for name in self._available if name not in BUILTIN_AGENTS
            ]
            logger.info(
                "Discovered custom agents %s in %s",
                " ".join(custom_names),
                " ".join(str(p) for p in self._search_paths),
            )

        self.active_profile = self._available.get(
            initial_agent, self._available[BuiltinAgentName.DEFAULT]
        )
        self._cached_config: VibeConfig | None = None

    @property
    def _config(self) -> VibeConfig:
        return self._config_getter()

    @property
    def available_agents(self) -> dict[str, AgentProfile]:
        if self._config.enabled_agents:
            return {
                name: profile
                for name, profile in self._available.items()
                if name_matches(name, self._config.enabled_agents)
            }
        if self._config.disabled_agents:
            return {
                name: profile
                for name, profile in self._available.items()
                if not name_matches(name, self._config.disabled_agents)
            }
        return dict(self._available)

    @property
    def config(self) -> VibeConfig:
        if self._cached_config is None:
            self._cached_config = self.active_profile.apply_to_config(self._config)
        return self._cached_config

    def switch_profile(self, name: str) -> None:
        self.active_profile = self.get_agent(name)
        self._cached_config = None

    def invalidate_config(self) -> None:
        self._cached_config = None

    @staticmethod
    def _compute_search_paths(config: VibeConfig) -> list[Path]:
        paths: list[Path] = []
        for path in config.agent_paths:
            if path.is_dir():
                paths.append(path)
        if (agents_dir := resolve_local_agents_dir(Path.cwd())) is not None:
            paths.append(agents_dir)
        if GLOBAL_AGENTS_DIR.path.is_dir():
            paths.append(GLOBAL_AGENTS_DIR.path)
        unique: list[Path] = []
        for p in paths:
            rp = p.resolve()
            if rp not in unique:
                unique.append(rp)
        return unique

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
