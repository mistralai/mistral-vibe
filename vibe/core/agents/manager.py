from __future__ import annotations

from typing import TYPE_CHECKING

from vibe.core.agents.diagnostics import excluded_agent_message
from vibe.core.agents.models import (
    BUILTIN_AGENTS,
    REMOVED_BUILTIN_AGENTS,
    AgentProfile,
    AgentSafety,
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
        # Agents chosen explicitly at startup (e.g. --smart-approve) stay in the
        # picker/cycle for the whole session even when the rollout gate would hide
        # them, so cycling away and back still returns to them.
        self._forced_agents: set[str] = set()

        if custom_names := [n for n in self._discovered if n not in BUILTIN_AGENTS]:
            logger.info(
                "Discovered custom agents %s in %s",
                " ".join(custom_names),
                " ".join(str(p) for p in self._registry.search_paths),
            )

        initial_agent = self._replace_removed_default_agent(initial_agent)
        profile = self.available_agents.get(initial_agent)
        if profile is None and initial_agent == BuiltinAgentName.SMART_APPROVE:
            # Explicitly asking for smart-approve (--smart-approve / --agent) is a
            # deliberate opt-in that bypasses the picker rollout gate. Remember the
            # opt-in so it keeps offering the mode for the rest of the session.
            profile = self._discovered.get(initial_agent)
            if profile is not None:
                self._forced_agents.add(initial_agent)
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

    def _replace_removed_default_agent(self, name: str) -> str:
        replacement = REMOVED_BUILTIN_AGENTS.get(name)
        if (
            replacement is None
            or name != self.config.default_agent
            or name in self._discovered
        ):
            return name
        logger.warning(
            "default_agent '%s' is no longer a builtin agent; starting on '%s'",
            name,
            replacement,
        )
        return replacement

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
        if name in self._forced_agents:
            # An explicit startup selection keeps the agent in the picker/cycle for
            # the whole session, regardless of the rollout gate.
            return True
        if not self.config.enable_auto_approve and profile.safety is AgentSafety.YOLO:
            # The policy setting can forbid the auto-approve (YOLO) mode outright. The
            # check keys on the safety level so custom yolo agents are covered
            # too, and it outranks the enabled_agents/disabled_agents lists below.
            return False
        if (
            name == BuiltinAgentName.SMART_APPROVE
            and not self.config.smart_approve_offered()
        ):
            # Smart approve ships dark: it enters the picker/cycle only for a cohort
            # the experiment (or config) has opted in. Explicit selection bypasses this
            # in __init__.
            return False
        if profile.install_required and name not in self.config.installed_agents:
            return False
        if enabled := self.config.enabled_agents:
            return name_matches(name, enabled)
        return not name_matches(name, self.config.disabled_agents)

    def switch_profile(self, name: str) -> None:
        self.active_profile = self.get_agent(name)
        self._install_profile(self.active_profile)

    def _install_profile(self, profile: AgentProfile) -> None:
        apply_profile_overrides(self._orchestrator, profile.overrides)

    def is_known_agent(self, name: str) -> bool:
        return name in self._discovered

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
            BuiltinAgentName.ACCEPT_EDITS,
            BuiltinAgentName.SMART_APPROVE,
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
