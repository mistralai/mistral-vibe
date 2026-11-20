from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import TYPE_CHECKING

from pydantic import ValidationError

from vibe.core.agents._migration import migrate_agent_profile_files
from vibe.core.agents.models import (
    BUILTIN_AGENTS,
    BUILTIN_APPROVAL_POLICIES,
    AgentProfile,
)
from vibe.core.config._setting_keys import UnusedConfigKeysError
from vibe.core.config.builder import InvalidConfigError
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.layer import ConfigLayer, RawConfig
from vibe.core.config.layers.agent_profile import (
    APPROVAL_POLICY_LAYER,
    AgentProfileLayer,
    refuse_profile_shell_timeouts,
)
from vibe.core.config.layers.project import ProjectConfigLayer
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

    Off the event loop only; code on it uses ``apply_profile_overrides_async``.
    """
    previous = _install_profile_layer(orchestrator, overrides, profile_name)
    try:
        orchestrator.rebuild()
    except Exception:
        _restore_profile_layer(orchestrator, previous)
        raise


async def apply_profile_overrides_async(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    overrides: dict[str, object],
    *,
    profile_name: str | None = None,
) -> None:
    """``apply_profile_overrides``, rebuilding on the event loop."""
    previous = _install_profile_layer(orchestrator, overrides, profile_name)
    try:
        await orchestrator.arebuild()
    except BaseException:
        _restore_profile_layer(orchestrator, previous)
        raise


def _install_profile_layer(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    overrides: dict[str, object],
    profile_name: str | None,
) -> ConfigLayer[RawConfig] | None:
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
    return previous


def _restore_profile_layer(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    previous: ConfigLayer[RawConfig] | None,
) -> None:
    if previous is None:
        orchestrator.remove_layer(len(orchestrator.layers) - 1)
    else:
        orchestrator.replace_or_append_layer(AgentProfileLayer.NAME, previous)


def apply_session_layers(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    agent: AgentProfile,
    policy: AgentProfile,
) -> None:
    """Install the session's agent and approval policy, then rebuild once.

    A refused rebuild puts both previous layers back. Off the event loop only;
    code on it uses ``apply_session_layers_async``.
    """
    previous = _install_session_layers(orchestrator, agent, policy)
    try:
        orchestrator.rebuild()
    except Exception:
        _restore_session_layers(orchestrator, previous)
        raise


async def apply_session_layers_async(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    agent: AgentProfile,
    policy: AgentProfile,
) -> None:
    previous = _install_session_layers(orchestrator, agent, policy)
    try:
        await orchestrator.arebuild()
    except BaseException:
        _restore_session_layers(orchestrator, previous)
        raise


type _PreviousLayers = tuple[tuple[str, ConfigLayer[RawConfig] | None], ...]


def _install_session_layers(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    agent: AgentProfile,
    policy: AgentProfile,
) -> _PreviousLayers:
    agent_overrides, policy_overrides = session_layer_overrides(agent, policy)
    previous = (
        (AgentProfileLayer.NAME, _layer_named(orchestrator, AgentProfileLayer.NAME)),
        (APPROVAL_POLICY_LAYER, _layer_named(orchestrator, APPROVAL_POLICY_LAYER)),
    )
    orchestrator.replace_or_append_layer(
        AgentProfileLayer.NAME,
        AgentProfileLayer(data=agent_overrides, profile_name=agent.name),
    )
    policy_layer = AgentProfileLayer(data=policy_overrides, name=APPROVAL_POLICY_LAYER)
    if previous[1][1] is not None:
        orchestrator.replace_or_append_layer(APPROVAL_POLICY_LAYER, policy_layer)
    else:
        # Right above the agent's, so the org's layer still outranks it.
        index = next(
            i
            for i, layer in enumerate(orchestrator.layers)
            if layer.name == AgentProfileLayer.NAME
        )
        orchestrator.insert_layer(policy_layer, index + 1)
    return previous


def _restore_session_layers(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], previous: _PreviousLayers
) -> None:
    for name, layer in reversed(previous):
        index = next(
            (
                i
                for i, existing in enumerate(orchestrator.layers)
                if existing.name == name
            ),
            None,
        )
        if layer is not None:
            orchestrator.replace_or_append_layer(name, layer)
        elif index is not None:
            orchestrator.remove_layer(index)


def _layer_named(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], name: str
) -> ConfigLayer[RawConfig] | None:
    return next((layer for layer in orchestrator.layers if layer.name == name), None)


_PERMISSION_STRICTNESS = {"never": 0, "ask": 1, "always": 2}
_NARROWING_PERMISSIONS = frozenset({"never", "ask"})


def agent_refused_tools(agent: AgentProfile) -> set[str]:
    """The tools ``agent`` sets to ``permission = "never"``."""
    tools = agent.overrides.get("tools")
    if not isinstance(tools, dict):
        return set()
    return {
        name
        for name, settings in tools.items()
        if isinstance(settings, dict) and settings.get("permission") == "never"
    }


def session_layer_overrides(
    agent: AgentProfile, policy: AgentProfile
) -> tuple[dict[str, object], dict[str, object]]:
    """The agent's and the policy's layers.

    What runs without asking belongs to the policy alone, so the agent's own
    bypass and its ``permission = "always"`` grants are dropped; an agent may
    only narrow, to ``ask`` or ``never``. A policy never loosens a tool
    permission the agent narrowed, and a bypassing policy, which skips every
    permission, disables the tools the agent set to ``never``.
    """
    agent_overrides = {
        key: value
        for key, value in agent.overrides.items()
        if key != "bypass_tool_permissions"
    }
    agent_tools = agent.overrides.get("tools")
    policy_overrides = dict(policy.overrides)
    if policy_overrides.get("bypass_tool_permissions") is True:
        if refused := sorted(agent_refused_tools(agent)):
            policy_overrides["disabled_tools"] = [
                *policy_overrides.get("disabled_tools", []),
                *refused,
            ]
    if isinstance(agent_tools, dict):
        agent_tools = {
            name: (
                {k: v for k, v in settings.items() if k != "permission"}
                if isinstance(settings, dict)
                and settings.get("permission") not in _NARROWING_PERMISSIONS
                else settings
            )
            for name, settings in agent_tools.items()
        }
        agent_overrides["tools"] = agent_tools
    policy_tools = policy.overrides.get("tools")
    if not isinstance(agent_tools, dict) or not isinstance(policy_tools, dict):
        return agent_overrides, policy_overrides
    tools: dict[str, object] = {}
    for name, settings in policy_tools.items():
        own = agent_tools.get(name)
        own_permission = own.get("permission") if isinstance(own, dict) else None
        wanted = settings.get("permission") if isinstance(settings, dict) else None
        if (
            isinstance(settings, dict)
            and isinstance(own_permission, str)
            and isinstance(wanted, str)
            and _PERMISSION_STRICTNESS.get(own_permission, 2)
            < _PERMISSION_STRICTNESS.get(wanted, 2)
        ):
            settings = {k: v for k, v in settings.items() if k != "permission"}
        tools[name] = settings
    return agent_overrides, {**policy_overrides, "tools": tools}


AGENT_SETTING_IGNORED = "agent.setting_ignored"
PROJECT_POLICY_OVERRIDE_IGNORED = "approval_policy.project_override_ignored"


@dataclass(frozen=True, slots=True)
class AgentFileIssue:
    """An agent file discovery kept only in part, or ignored, and why.

    ``code`` is machine-readable; ``key`` is the dotted setting an agent may
    not set, for ``agent.setting_ignored``.
    """

    code: str
    name: str
    path: Path
    message: str
    key: str | None = None


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
        self.issues: tuple[AgentFileIssue, ...] = ()
        self.policies: dict[str, AgentProfile] = dict(BUILTIN_APPROVAL_POLICIES)
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
        issues: list[AgentFileIssue] = []
        project_dirs = self._project_scoped_dirs()
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
                if agent is not None and agent.name in BUILTIN_APPROVAL_POLICIES:
                    if base.resolve() in project_dirs:
                        # What runs without asking is the user's or the org's
                        # call, never a repository's.
                        issues.append(
                            AgentFileIssue(
                                code=PROJECT_POLICY_OVERRIDE_IGNORED,
                                name=agent.name,
                                path=agent_file,
                                message=(
                                    f"A project cannot redefine the approval policy "
                                    f"'{agent.name}'; this file is ignored. Move it "
                                    "to ~/.vibe/agents/ to override the policy."
                                ),
                            )
                        )
                        continue
                    self._override_policy(agent, agent_file)
                    continue
                if agent is not None:
                    issues.extend(
                        AgentFileIssue(
                            code=AGENT_SETTING_IGNORED,
                            name=agent.name,
                            path=agent_file,
                            key=key,
                            message=(
                                f"Agent '{agent.name}' sets '{key}', which an agent "
                                "may not set; it is ignored. What runs without "
                                "asking is the approval policy's."
                            ),
                        )
                        for key in agent.ignored_settings
                    )
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
        self.issues = tuple(issues)
        return agents

    def _project_scoped_dirs(self) -> set[Path]:
        """The agent directories a repository supplies: its `.vibe/agents`, and
        the `agent_paths` its own `.vibe/config.toml` adds.
        """
        dirs = {path.resolve() for path in self._harness_files.project_agents_dirs}
        project = _layer_named(self._orchestrator, ProjectConfigLayer.NAME)
        data = project.cached_data if project is not None else None
        paths = getattr(data, "agent_paths", None)
        if isinstance(paths, list):
            dirs.update(
                Path(path).expanduser().resolve()
                for path in paths
                if isinstance(path, str | Path)
            )
        return dirs

    def _override_policy(self, policy: AgentProfile, path: Path) -> None:
        current = self.policies[policy.name]
        if current is not BUILTIN_APPROVAL_POLICIES[policy.name]:
            logger.debug("Skipping duplicate policy '%s' at %s", policy.name, path)
            return
        if policy.config_error is not None:
            # A broken override keeps the builtin, so the session still starts.
            logger.warning(
                "Ignoring the override of approval policy '%s' at %s: %s",
                policy.name,
                path,
                policy.config_error,
            )
            return
        logger.info("Custom file overrides approval policy '%s'", policy.name)
        self.policies[policy.name] = policy

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
