from __future__ import annotations

from typing import TYPE_CHECKING, Final

from vibe.core.agents.diagnostics import excluded_agent_message
from vibe.core.agents.models import (
    BUILTIN_AGENTS,
    REMOVED_BUILTIN_AGENTS,
    AgentProfile,
    AgentSafety,
    AgentType,
    BuiltinAgentName,
)
from vibe.core.agents.registry import (
    AgentLoadFailure,
    AgentRegistry,
    apply_profile_overrides,
    refuse_profile_shell_timeouts_over_the_users,
)
from vibe.core.config.builder import InvalidConfigError
from vibe.core.config.harness_files import (
    HarnessFilesManager,
    get_harness_files_manager,
)
from vibe.core.config.layers.admin import admin_layer, enforced_keys
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.utils import name_matches
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema

# The modes a session falls back to, in order, after the default.
FALLBACK_AGENTS: Final = (BuiltinAgentName.ACCEPT_EDITS, BuiltinAgentName.ASK)


def resolve_default_agent(orchestrator: ConfigOrchestrator[VibeConfigSchema]) -> str:
    """The agent a session starts in when none is asked for.

    An org-enforced ``default_agent`` wins; otherwise the smart-approve
    experiment's cohort default, then ``default_agent``.
    ``smart_approve_default`` stays the experiment's rollout knob: an org can
    still enforce it to opt a cohort in or out, but it never beats an enforced
    ``default_agent`` -- an org that wants smart-approve as the default
    enforces ``default_agent = "smart-approve"``.
    """
    config = orchestrator.config
    if "default_agent" in enforced_keys(orchestrator):
        return config.default_agent
    if config.smart_approve_default:
        return BuiltinAgentName.SMART_APPROVE
    return config.default_agent


class AgentManager:
    def __init__(
        self,
        orchestrator: ConfigOrchestrator[VibeConfigSchema],
        initial_agent: str | None = BuiltinAgentName.ACCEPT_EDITS,
        allow_subagent: bool = False,
        harness_files: HarnessFilesManager | None = None,
    ) -> None:
        """``initial_agent=None`` starts the session's default
        (``resolve_default_agent``).
        """
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

        self.excluded_agent: str | None = None
        """The ``initial_agent`` the org config excludes, if the manager started
        another mode instead. An excluded default is not recorded: it would
        repeat every session, and the mode indicator already shows the mode."""
        self.active_profile = self._start_profile(
            initial_agent, allow_subagent=allow_subagent
        )
        self._install_profile(self.active_profile)

    def _start_profile(
        self, requested: str | None, *, allow_subagent: bool
    ) -> AgentProfile:
        """The profile to start in; the org config's agent rules never stop it.

        The org config is data the user cannot fix, so a mode it makes
        unavailable starts another one instead: the session's default (the
        org's, when it sets one) when it can, else a builtin. That covers an
        enforced default this checkout cannot resolve (a custom agent that
        exists only in another checkout), and a default or requested mode the
        org excludes (``org_excludes``). Any other failure keeps its loud error: a
        typo, or a subagent, asked for or saved as the default.
        """
        default = resolve_default_agent(self._orchestrator)
        desired = requested if requested is not None else default
        starting = self._replace_removed_default_agent(desired)
        try:
            return self._profile_for(starting, allow_subagent=allow_subagent)
        except ValueError as exc:
            enforced_default = requested is None and "default_agent" in enforced_keys(
                self._orchestrator
            )
            excluded_mode = self._is_primary(starting) and self.org_excludes(starting)
            if not (enforced_default or excluded_mode):
                raise
            error = exc
        for candidate in (default, *FALLBACK_AGENTS):
            if candidate in {desired, starting}:
                continue
            try:
                profile = self._profile_for(candidate, allow_subagent=False)
            except ValueError as exc:
                error = exc
                continue
            logger.info(
                "Agent '%s' is unavailable under the org config; starting '%s'",
                desired,
                profile.name,
            )
            self.excluded_agent = requested
            return profile
        # Every fallback excluded too: a broken configuration, kept loud.
        raise error

    def _profile_for(self, initial_agent: str, *, allow_subagent: bool) -> AgentProfile:
        initial_agent = self._replace_removed_default_agent(initial_agent)
        profile = self.available_agents.get(initial_agent)
        if (
            profile is None
            and initial_agent == BuiltinAgentName.SMART_APPROVE
            and not self.org_excludes(initial_agent)
        ):
            # Explicitly asking for smart-approve (--smart-approve / --agent) is a
            # deliberate opt-in that bypasses the picker rollout gate, though not
            # the org's agent lists. Remember the opt-in so it keeps offering the
            # mode for the rest of the session.
            profile = self._discovered.get(initial_agent)
            if profile is not None:
                self._forced_agents.add(initial_agent)
        if profile is None:
            if initial_agent in self._discovered:
                raise ValueError(self._unavailable_message(initial_agent))
            raise ValueError(f"Agent '{initial_agent}' not found.")
        if not allow_subagent and profile.agent_type != AgentType.AGENT:
            raise ValueError(
                f"Agent '{initial_agent}' is a {profile.agent_type} and cannot be used"
                f" as the primary agent. Only agents of type 'agent' can be selected"
                f" with --agent."
            )
        return profile

    def _is_primary(self, name: str) -> bool:
        profile = self._discovered.get(name)
        return profile is not None and profile.agent_type == AgentType.AGENT

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
    def load_failures(self) -> tuple[AgentLoadFailure, ...]:
        return self._registry.failures

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

    def org_excludes(self, name: str) -> bool:
        """Whether the org config itself leaves ``name`` out: its own
        ``enabled_agents``/``disabled_agents``, or ``enable_auto_approve =
        false`` for an agent with the ``yolo`` safety level.
        """
        if _org_lists_exclude(self._orchestrator, name):
            return True
        profile = self._discovered.get(name)
        return (
            profile is not None
            and profile.safety is AgentSafety.YOLO
            and _org_forbids_auto_approve(self._orchestrator)
        )

    def _is_agent_available(self, name: str, profile: AgentProfile) -> bool:
        if self.org_excludes(name) or (
            not self.config.enable_auto_approve and profile.safety is AgentSafety.YOLO
        ):
            # The org's own lists are checked apart from the merged lists below,
            # which also hold the user's entries: a local enabled_agents would
            # widen the org's. enable_auto_approve = false forbids the
            # auto-approve (YOLO) mode outright, keyed on the safety level so
            # custom yolo agents are covered too.
            return False
        if name in self._forced_agents:
            # An explicit startup selection keeps the agent in the picker/cycle for
            # the whole session, regardless of the rollout gate.
            return True
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
        profile = self.get_agent(name)
        # A refused profile leaves the session on the one it was running.
        self._install_profile(profile)
        self.active_profile = profile

    def _install_profile(self, profile: AgentProfile) -> None:
        if profile.config_error is not None:
            raise InvalidConfigError(
                f"Agent '{profile.name}' in {profile.source_path}:"
                f" {profile.config_error}"
            )
        refuse_profile_shell_timeouts_over_the_users(self._orchestrator, profile)
        apply_profile_overrides(
            self._orchestrator, profile.overrides, profile_name=profile.name
        )

    def is_known_agent(self, name: str) -> bool:
        return name in self._discovered

    def get_agent(self, name: str) -> AgentProfile:
        if agent := self.available_agents.get(name):
            return agent
        if name in self._discovered:
            raise ValueError(self._unavailable_message(name))
        raise ValueError(f"Agent '{name}' not found")

    def _unavailable_message(self, name: str) -> str:
        # Advice to edit the agent lists cannot help against the org's own.
        if self.org_excludes(name):
            return f"Agent '{name}' is disabled by your organization."
        return excluded_agent_message(name, self.config, self._discovered)

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


def _org_lists_exclude(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], name: str
) -> bool:
    """Whether the org config's own ``enabled_agents``/``disabled_agents``
    leave ``name`` out.
    """
    enabled = _org_agent_list(orchestrator, "enabled_agents")
    if enabled and not name_matches(name, enabled):
        return True
    return name_matches(name, _org_agent_list(orchestrator, "disabled_agents"))


def _org_forbids_auto_approve(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
) -> bool:
    return _org_value(orchestrator, "enable_auto_approve") is False


def _org_agent_list(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], key: str
) -> list[str]:
    value = _org_value(orchestrator, key)
    return [str(item) for item in value] if isinstance(value, list) else []


def _org_value(orchestrator: ConfigOrchestrator[VibeConfigSchema], key: str) -> object:
    layer = admin_layer(orchestrator)
    return layer.snapshot().get(key) if layer is not None else None
