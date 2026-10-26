from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import TYPE_CHECKING

from pydantic import ValidationError

from vibe.core.agents._migration import migrate_agent_profile_files
from vibe.core.agents.models import BUILTIN_AGENTS, AgentProfile
from vibe.core.config._setting_keys import UnusedConfigKeysError
from vibe.core.config.builder import InvalidConfigError
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layers.agent_profile import (
    AgentProfileLayer,
    refuse_profile_shell_timeouts,
)
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.paths import dedup_paths
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema


def apply_profile_overrides(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    overrides: dict[str, object],
    *,
    profile_name: str | None = None,
) -> None:
    """Fill the orchestrator's agent-profile layer with *overrides*, then rebuild.

    With *profile_name*, the running agent's, the layer refuses any later build
    in which its shell timeouts would replace a config's.

    A rebuild that refuses the overrides puts the previous layer back, so the
    config the orchestrator holds and its layers still agree.
    """
    previous = next(
        (
            layer
            for layer in orchestrator.layers
            if layer.name == AgentProfileLayer.NAME
        ),
        None,
    )
    orchestrator.replace_or_append_layer(
        AgentProfileLayer.NAME,
        AgentProfileLayer(data=overrides, profile_name=profile_name),
    )
    try:
        orchestrator.rebuild()
    except Exception:
        if previous is None:
            orchestrator.remove_layer(len(orchestrator.layers) - 1)
        else:
            orchestrator.replace_or_append_layer(AgentProfileLayer.NAME, previous)
        raise


@dataclass(frozen=True, slots=True)
class AgentLoadFailure:
    """An agent file discovery dropped, and why."""

    name: str
    path: Path
    reason: str


def refuse_profile_shell_timeouts_over_the_users(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], profile: AgentProfile
) -> None:
    """Refuse to install a profile whose shell timeouts would replace a config's.

    Checked before the profile's layer replaces the running one, so a refused
    switch leaves the running agent as it was. Once installed, its layer checks
    every rebuild, reload and write the same way.
    """
    lower: list[tuple[str, object]] = []
    for layer in orchestrator.layers:
        if layer.name == AgentProfileLayer.NAME:
            break
        if layer.cached_data is not None:
            lower.append((layer.name, layer.cached_data))
    refuse_profile_shell_timeouts(profile.name, profile.overrides, lower)


class AgentRegistry:
    """Discovers and parses agent-profile definitions from the filesystem.

    Owns search-path resolution, migration, and TOML loading. Each custom
    profile is validated by folding it onto a throwaway orchestrator copy. A file
    that does not parse is dropped at discovery; a custom profile whose settings
    break a config rule is kept with its ``config_error``, which choosing it
    raises. Such an override of a builtin is dropped, keeping the builtin.
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
        self.failures: tuple[AgentLoadFailure, ...] = ()
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
        failures: list[AgentLoadFailure] = []
        candidate: ConfigOrchestrator[VibeConfigSchema] | None = None

        for base in self.search_paths:
            if not base.is_dir():
                continue
            for agent_file in base.glob("*.toml"):
                if not agent_file.is_file():
                    continue
                if candidate is None:
                    candidate = self._orchestrator.copy()
                agent = self._try_load(agent_file, candidate, failures, agents)
                if agent is not None:
                    if agent.name in BUILTIN_AGENTS and agent.config_error is not None:
                        # A broken override of a builtin mode keeps the builtin,
                        # so the session still starts in its default mode.
                        logger.warning(
                            "Ignoring the override of builtin agent '%s' at %s: %s",
                            agent.name,
                            agent_file,
                            agent.config_error,
                        )
                        continue
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

        self.failures = tuple(failures)
        return agents

    def _try_load(
        self,
        agent_file: Path,
        candidate: ConfigOrchestrator[VibeConfigSchema],
        failures: list[AgentLoadFailure],
        loaded: dict[str, AgentProfile],
    ) -> AgentProfile | None:
        try:
            agent = AgentProfile.from_toml(agent_file)
            try:
                apply_profile_overrides(candidate, agent.overrides)
            except UnusedConfigKeysError:
                # Settings the profile does not use are reported when it is in
                # use, where strict_config refuses them, rather than hiding it.
                pass
            except ValidationError as error:
                problems = str(InvalidConfigError.from_validation_error(error))
                logger.warning("Agent at %s is invalid: %s", agent_file, problems)
                # Kept, so choosing it gives this error, and reported now like a
                # file that does not load: no surface runs it until it is fixed.
                _record_failure(failures, loaded, agent_file, problems)
                return dataclasses.replace(agent, config_error=problems)
            return agent
        except Exception as e:
            logger.warning("Failed to load agent at %s: %s", agent_file, e)
            _record_failure(failures, loaded, agent_file, _reason(e))
            return None


def _record_failure(
    failures: list[AgentLoadFailure],
    loaded: dict[str, AgentProfile],
    agent_file: Path,
    reason: str,
) -> None:
    # A file an earlier search path already provides is shadowed: its
    # failure changes nothing the session runs.
    shadowing = loaded.get(agent_file.stem)
    if shadowing is None or shadowing.source_path is None:
        failures.append(
            AgentLoadFailure(name=agent_file.stem, path=agent_file, reason=reason)
        )


def _reason(error: Exception) -> str:
    match error:
        case ValidationError():
            return "; ".join(
                f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
                if item["loc"]
                else item["msg"]
                for item in error.errors()
            )
        case tomllib.TOMLDecodeError():
            return f"invalid TOML: {error}"
        case _:
            return str(error) or type(error).__name__
