from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from vibe.core.agents._migration import migrate_agent_profile_files
from vibe.core.agents.models import BUILTIN_AGENTS, AgentProfile
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layers.agent_profile import AgentProfileLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.paths import dedup_paths
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema


def apply_profile_overrides(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], overrides: dict[str, object]
) -> None:
    """Fill the orchestrator's agent-profile layer with *overrides*, then rebuild."""
    orchestrator.replace_or_append_layer(
        AgentProfileLayer.NAME, AgentProfileLayer(data=overrides)
    )
    orchestrator.rebuild()


class AgentRegistry:
    """Discovers and parses agent-profile definitions from the filesystem.

    Owns search-path resolution, migration, and TOML loading. Each custom
    profile is validated by folding it onto a throwaway orchestrator copy, so a
    broken definition is dropped at discovery rather than at selection.
    """

    def __init__(
        self,
        orchestrator: ConfigOrchestrator[VibeConfigSchema],
        harness_files: HarnessFilesManager,
    ) -> None:
        self._orchestrator = orchestrator
        self._harness_files = harness_files
        self.search_paths = self._compute_search_paths()
        self._migrate()
        self.discovered = self._discover()

    def rediscover(self, harness_files: HarnessFilesManager) -> None:
        """Point discovery at a different set of project directories.

        Search paths are resolved once at construction, so a session that has
        moved keeps offering the agents of the directory it left until this runs.
        """
        self._harness_files = harness_files
        self.search_paths = self._compute_search_paths()
        self._migrate()
        self.discovered = self._discover()

    def _compute_search_paths(self) -> list[Path]:
        mgr = self._harness_files
        return dedup_paths([
            *(p for p in self._orchestrator.config.agent_paths if p.is_dir()),
            *mgr.project_agents_dirs,
            *mgr.user_agents_dirs,
        ])

    def _migrate(self) -> None:
        try:
            migrate_agent_profile_files(self.search_paths)
        except Exception as exc:
            logger.warning("Failed to migrate agent profiles", exc_info=exc)

    def _discover(self) -> dict[str, AgentProfile]:
        agents: dict[str, AgentProfile] = dict(BUILTIN_AGENTS)
        candidate: ConfigOrchestrator[VibeConfigSchema] | None = None

        for base in self.search_paths:
            if not base.is_dir():
                continue
            for agent_file in base.glob("*.toml"):
                if not agent_file.is_file():
                    continue
                if candidate is None:
                    candidate = self._orchestrator.copy()
                if (agent := self._try_load(agent_file, candidate)) is not None:
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

    def _try_load(
        self, agent_file: Path, candidate: ConfigOrchestrator[VibeConfigSchema]
    ) -> AgentProfile | None:
        try:
            agent = AgentProfile.from_toml(agent_file)
            apply_profile_overrides(candidate, agent.overrides)
            return agent
        except Exception as e:
            logger.warning("Failed to load agent at %s: %s", agent_file, e)
            return None
