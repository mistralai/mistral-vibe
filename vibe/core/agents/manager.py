from __future__ import annotations

import copy
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from vibe.core.agents.diagnostics import excluded_agent_message
from vibe.core.agents.models import (
    BUILTIN_AGENTS,
    REMOVED_BUILTIN_AGENTS,
    AgentProfile,
    AgentSafety,
    AgentType,
    ApprovalPolicyName,
    BuiltinAgentName,
)
from vibe.core.agents.registry import (
    AgentFileIssue,
    AgentLoadFailure,
    AgentRegistry,
    apply_session_layers,
    apply_session_layers_async,
    refuse_profile_shell_timeouts_over_the_users,
)
from vibe.core.config.builder import InvalidConfigError
from vibe.core.config.harness_files import (
    HarnessFilesManager,
    get_harness_files_manager,
)
from vibe.core.config.layers.admin import AdminConfigLayer, admin_layer
from vibe.core.config.layers.default import DefaultConfigLayer
from vibe.core.config.layers.growthbook import GrowthbookLayer
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.utils import name_matches
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema

FALLBACK_POLICIES: Final = (ApprovalPolicyName.ACCEPT_EDITS, ApprovalPolicyName.ASK)

POLICY_ORDER: Final = (
    ApprovalPolicyName.ASK,
    ApprovalPolicyName.ACCEPT_EDITS,
    ApprovalPolicyName.SMART_APPROVE,
    ApprovalPolicyName.AUTO_APPROVE,
)


class SelectionRefusal(StrEnum):
    """Why an agent or a policy cannot be selected, for clients to word."""

    NOT_FOUND = "not_found"
    DISABLED_BY_ORGANIZATION = "disabled_by_organization"
    AUTO_APPROVE_DISABLED = "auto_approve_disabled"
    INSTALL_REQUIRED = "install_required"
    SUBAGENT = "subagent"
    UNAVAILABLE = "unavailable"


class AgentSelectionError(ValueError):
    """An agent or policy name the session cannot run."""

    def __init__(self, message: str, reason: SelectionRefusal) -> None:
        super().__init__(message)
        self.reason = reason


class AgentManager:
    """The session's agent and its approval policy, chosen independently.

    The agent says what the session is (instructions, model, tools); the policy
    says which tool calls run without asking. Both come from the same files:
    a file named after a built-in policy overrides that policy, any other file
    is an agent.
    """

    def __init__(
        self,
        orchestrator: ConfigOrchestrator[VibeConfigSchema],
        initial_agent: str | None = None,
        allow_subagent: bool = False,
        harness_files: HarnessFilesManager | None = None,
        *,
        initial_policy: str | None = None,
    ) -> None:
        """``None`` starts the configured default. A policy name given as
        ``initial_agent`` is read as ``initial_policy``, as before the split.
        """
        self._orchestrator = orchestrator
        self._registry = AgentRegistry(
            orchestrator, harness_files or get_harness_files_manager()
        )
        # Policies chosen explicitly at startup (e.g. --smart-approve) stay in the
        # picker/cycle for the whole session even when the rollout gate would hide
        # them, so cycling away and back still returns to them.
        self._forced_policies: set[str] = set()

        if custom_names := [n for n in self._discovered if n not in BUILTIN_AGENTS]:
            logger.info(
                "Discovered custom agents %s in %s",
                " ".join(custom_names),
                " ".join(str(p) for p in self._registry.search_paths),
            )

        if initial_agent is not None and initial_agent in self._registry.policies:
            initial_policy = initial_policy or initial_agent
            initial_agent = None

        self.excluded_agent: str | None = None
        """The requested agent or policy the org config excludes, if the
        manager started another one instead. An excluded default is not
        recorded: it would repeat every session, and the indicator already
        shows what runs."""
        self.restore_warnings: list[str] = []
        """What a reopened session could not get back of what it saved."""
        self.active_agent = self._start_agent(
            initial_agent, allow_subagent=allow_subagent
        )
        self.active_policy = self._start_policy(initial_policy)
        self._install(self.active_agent, self.active_policy)

    def _start_agent(
        self, requested: str | None, *, allow_subagent: bool
    ) -> AgentProfile:
        default = self.config.default_agent
        if self._removed_default_policy() is not None:
            logger.warning(
                "default_agent '%s' is no longer a builtin agent; starting on the"
                " '%s' policy",
                default,
                REMOVED_BUILTIN_AGENTS[default],
            )
            default = BuiltinAgentName.DEFAULT
        desired = requested if requested is not None else default
        try:
            return self._agent_for(desired, allow_subagent=allow_subagent)
        except AgentSelectionError as exc:
            org_excluded = self._is_primary(desired) and self.org_excludes(desired)
            if requested is None:
                if (
                    self.config.origin_of("default_agent")
                    in {AdminConfigLayer.NAME, GrowthbookLayer.NAME}
                    and exc.reason is not SelectionRefusal.SUBAGENT
                ) or org_excluded:
                    fallback = self._fallback_agent(exc)
                    logger.info(
                        "Default agent '%s' is unavailable; starting '%s'",
                        desired,
                        fallback.name,
                    )
                    return fallback
                message = str(exc)
                if not message.startswith("default_agent"):
                    message = f"default_agent: {message}"
                raise InvalidConfigError(message) from exc
            if not org_excluded:
                raise
            fallback = self._fallback_agent(exc)
            logger.info(
                "Agent '%s' is unavailable; starting '%s'", desired, fallback.name
            )
            self.excluded_agent = requested
            return fallback

    def _fallback_agent(self, error: AgentSelectionError) -> AgentProfile:
        primary = self.first_primary_agent()
        if primary is None:
            raise error
        return primary

    def first_primary_agent(self) -> AgentProfile | None:
        """The default agent, or the first an allowlist leaving it out allows."""
        primary = self.primary_agents()
        return primary[0] if primary else None

    def _agent_for(self, name: str, *, allow_subagent: bool) -> AgentProfile:
        profile = self._discovered.get(name)
        if (
            profile is None
            or (
                # A subagent is refused as one, unless the org's lists leave it
                # out: that refusal names the org.
                (allow_subagent or profile.agent_type is AgentType.AGENT)
                or self.org_excludes(name)
            )
            and name not in self.available_agents
        ):
            raise self._agent_refusal(name)
        if not allow_subagent and profile.agent_type != AgentType.AGENT:
            raise AgentSelectionError(
                f"Agent '{name}' is a {profile.agent_type} and cannot be used"
                f" as the primary agent. Only agents of type 'agent' can be selected"
                f" with --agent.",
                SelectionRefusal.SUBAGENT,
            )
        return profile

    def _agent_refusal(self, name: str) -> AgentSelectionError:
        profile = self._discovered.get(name)
        if profile is None and name in REMOVED_BUILTIN_AGENTS:
            return self._policy_refusal(name)
        if profile is None:
            return AgentSelectionError(
                f"Agent '{name}' not found.", SelectionRefusal.NOT_FOUND
            )
        if self.org_excludes(name):
            return AgentSelectionError(
                f"Agent '{name}' is disabled by your organization.",
                SelectionRefusal.DISABLED_BY_ORGANIZATION,
            )
        reason = (
            SelectionRefusal.INSTALL_REQUIRED
            if profile.install_required and name not in self.config.installed_agents
            else SelectionRefusal.UNAVAILABLE
        )
        return AgentSelectionError(
            excluded_agent_message(name, self.config, self._discovered), reason
        )

    @property
    def _discovered(self) -> dict[str, AgentProfile]:
        return self._registry.discovered

    @property
    def load_failures(self) -> tuple[AgentLoadFailure, ...]:
        return self._registry.failures

    @property
    def agent_file_issues(self) -> tuple[AgentFileIssue, ...]:
        return self._registry.issues

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

    def primary_agents(self) -> list[AgentProfile]:
        """The agents a user may pick, the default first."""
        available = self.available_agents
        default = available.get(BuiltinAgentName.DEFAULT)
        custom = sorted(
            (
                profile
                for name, profile in available.items()
                if name != BuiltinAgentName.DEFAULT
                and profile.agent_type is AgentType.AGENT
            ),
            key=lambda profile: profile.name,
        )
        return [*([default] if default is not None else []), *custom]

    def _is_agent_available(self, name: str, profile: AgentProfile) -> bool:
        if self.org_excludes(name):
            return False
        if profile.install_required and name not in self.config.installed_agents:
            return False
        if (enabled := self.config.enabled_agents) and self._names_any_agent(enabled):
            return name_matches(name, enabled) or self._keeps_default_agent(
                name, enabled
            )
        return not name_matches(name, self.config.disabled_agents)

    def _keeps_default_agent(self, name: str, enabled: list[str]) -> bool:
        """A list that names policies as well as agents keeps the default
        agent: before the split its policy names were the modes the default
        agent ran in, and dropping it would refuse configs that started.
        """
        return name == BuiltinAgentName.DEFAULT and self._names_any_policy(enabled)

    def org_excludes(self, name: str) -> bool:
        """Whether the org config itself leaves ``name`` out: its own
        ``enabled_agents``/``disabled_agents``, or ``enable_auto_approve =
        false`` for a policy with the ``yolo`` safety level.

        An org allowlist of agents that names no policy allows only ``ask``.
        """
        policy = self._registry.policies.get(name)
        if policy is not None and policy.safety is AgentSafety.YOLO:
            if _org_forbids_auto_approve(self._orchestrator):
                return True
        enabled = _org_agent_list(self._orchestrator, "enabled_agents")
        if enabled and not self._allowlist_admits(name, enabled, org=True):
            return True
        return name_matches(
            name, _org_agent_list(self._orchestrator, "disabled_agents")
        )

    def _allowlist_admits(
        self, name: str, enabled: list[str], *, org: bool = False
    ) -> bool:
        """Whether an ``enabled_agents`` list admits an agent or a policy.

        A list naming no agent leaves the default agent, as does one naming
        policies too; a list of agents that names no policy admits only
        ``ask``: an agent never brings a policy. A user's list naming nothing
        it knows leaves the policies alone; the org's admits only ``ask``, so
        a typo there never opens every policy.
        """
        if name in self._registry.policies:
            if self._names_any_policy(enabled):
                return name_matches(name, enabled)
            if not org and not self._names_any_agent(enabled):
                return True
            return name == ApprovalPolicyName.ASK
        if self._names_any_agent(enabled):
            return name_matches(name, enabled) or self._keeps_default_agent(
                name, enabled
            )
        return name == BuiltinAgentName.DEFAULT or name not in self._discovered

    def _only_agents_allowlisted(self) -> bool:
        """Whether ``enabled_agents`` lists agents only, which bounds the
        policies without the user naming one: a default it leaves out is no
        config error, the start takes a policy it allows.
        """
        enabled = self.config.enabled_agents
        return (
            bool(enabled)
            and not self._names_any_policy(enabled)
            and self._names_any_agent(enabled)
        )

    def _names_any_policy(self, patterns: list[str]) -> bool:
        return any(name_matches(name, patterns) for name in self._registry.policies)

    def _names_any_agent(self, patterns: list[str]) -> bool:
        return any(
            name_matches(name, patterns)
            for name, profile in self._discovered.items()
            if profile.agent_type is AgentType.AGENT
        )

    def is_known_agent(self, name: str) -> bool:
        return name in self._discovered

    def get_agent(self, name: str) -> AgentProfile:
        if agent := self.available_agents.get(name):
            return agent
        raise self._agent_refusal(name)

    def get_subagents(self) -> list[AgentProfile]:
        return [
            a
            for a in self.available_agents.values()
            if a.agent_type == AgentType.SUBAGENT
        ]

    def _is_primary(self, name: str) -> bool:
        profile = self._discovered.get(name)
        return profile is not None and profile.agent_type is AgentType.AGENT

    def _removed_default_policy(self) -> str | None:
        """The policy a removed builtin ``default_agent`` (``plan``) starts on,
        unless a custom agent took its name.
        """
        name = self.config.default_agent
        if name in self._discovered:
            return None
        return REMOVED_BUILTIN_AGENTS.get(name)

    def _removed_default_policy_applies(self) -> str | None:
        """``_removed_default_policy`` unless ``default_approval_policy`` is
        set, as the warning about the removed agent asks.
        """
        if self.config.origin_of("default_approval_policy") not in {
            None,
            DefaultConfigLayer.NAME,
        }:
            return None
        return self._removed_default_policy()

    def is_policy_name(self, name: str) -> bool:
        """Whether ``name`` names an approval policy rather than an agent, as
        clients from before the split send it as an agent.
        """
        return self._names_a_policy(name)

    def _names_a_policy(self, name: str) -> bool:
        return name in self._registry.policies or (
            name in REMOVED_BUILTIN_AGENTS and name not in self._discovered
        )

    def _start_policy(self, requested: str | None) -> AgentProfile:
        """The policy to start in; the org config's rules never stop it.

        Order: the request, then the configured default. A policy the org or the rollout makes unavailable
        starts the next one; a typo stays loud.
        """
        if requested is not None:
            try:
                return self._policy_for(requested, opt_in=True)
            except AgentSelectionError as exc:
                if not self.org_excludes(requested):
                    raise
                self.excluded_agent = requested
                error: AgentSelectionError = exc
        else:
            default = self.config.default_approval_policy
            origin = self.config.origin_of("default_approval_policy")
            if (removed := self._removed_default_policy_applies()) is not None:
                default = removed
                origin = self.config.origin_of("default_agent")
            try:
                return self._policy_for(default, opt_in=origin != GrowthbookLayer.NAME)
            except AgentSelectionError as exc:
                if exc.reason is SelectionRefusal.NOT_FOUND or (
                    origin not in {AdminConfigLayer.NAME, GrowthbookLayer.NAME}
                    and not self.org_excludes(default)
                    and not self._only_agents_allowlisted()
                    and exc.reason is not SelectionRefusal.AUTO_APPROVE_DISABLED
                ):
                    message = str(exc)
                    if not message.startswith("default_approval_policy"):
                        message = f"default_approval_policy: {message}"
                    raise InvalidConfigError(message) from exc
                error = exc
        default = (
            self._removed_default_policy_applies()
            or self.config.default_approval_policy
        )
        for candidate in (default, *FALLBACK_POLICIES, *self.get_policy_order()):
            try:
                profile = self._policy_for(candidate, opt_in=False)
            except AgentSelectionError:
                continue
            logger.info("Approval policy unavailable; starting '%s'", profile.name)
            return profile
        raise error

    def _policy_for(self, name: str, *, opt_in: bool) -> AgentProfile:
        profile = self.available_policies.get(name)
        if (
            profile is None
            and opt_in
            and name == ApprovalPolicyName.SMART_APPROVE
            and not self.org_excludes(name)
        ):
            # Asking for smart-approve (--smart-approve, or a default a person or
            # org set) is a deliberate opt-in that bypasses the picker rollout
            # gate, though not the org's lists. A default the experiment set is
            # no opt-in.
            profile = self._registry.policies.get(name)
            if profile is not None:
                self._forced_policies.add(name)
        if profile is None:
            raise self._policy_refusal(name)
        return profile

    def _policy_refusal(self, name: str) -> AgentSelectionError:
        profile = self._registry.policies.get(name)
        if profile is None:
            if (replacement := REMOVED_BUILTIN_AGENTS.get(name)) is not None:
                return AgentSelectionError(
                    f"Agent '{name}' was removed in Vibe 3.0."
                    f" Use '{replacement}' or a custom agent.",
                    SelectionRefusal.NOT_FOUND,
                )
            return AgentSelectionError(
                f"Approval policy '{name}' not found.", SelectionRefusal.NOT_FOUND
            )
        if self.org_excludes(name):
            return AgentSelectionError(
                f"Approval policy '{name}' is disabled by your organization.",
                SelectionRefusal.DISABLED_BY_ORGANIZATION,
            )
        if profile.safety is AgentSafety.YOLO and not self.config.enable_auto_approve:
            return AgentSelectionError(
                f"Approval policy '{name}' is unavailable: the auto-approve mode is"
                " disabled ('enable_auto_approve' is false).",
                SelectionRefusal.AUTO_APPROVE_DISABLED,
            )
        return AgentSelectionError(
            excluded_agent_message(
                name, self.config, self._registry.policies, policy=True
            ),
            SelectionRefusal.UNAVAILABLE,
        )

    @property
    def available_policies(self) -> dict[str, AgentProfile]:
        return {
            name: profile
            for name, profile in self._registry.policies.items()
            if self._is_policy_available(name, profile)
        }

    def _is_policy_available(self, name: str, profile: AgentProfile) -> bool:
        if self.org_excludes(name) or (
            not self.config.enable_auto_approve and profile.safety is AgentSafety.YOLO
        ):
            return False
        if name in self._forced_policies:
            return True
        if (
            name == ApprovalPolicyName.SMART_APPROVE
            and not self.config.smart_approve_available
        ):
            # Smart approve ships dark: it enters the picker/cycle only for a cohort
            # the experiment (or config) has opted in.
            return False
        if enabled := self.config.enabled_agents:
            return self._allowlist_admits(name, enabled)
        return not name_matches(name, self.config.disabled_agents)

    def get_policy(self, name: str) -> AgentProfile:
        if policy := self.available_policies.get(name):
            return policy
        raise self._policy_refusal(name)

    def get_policy_order(self) -> list[str]:
        available = self.available_policies
        order = [name for name in POLICY_ORDER if name in available]
        return order + sorted(name for name in available if name not in order)

    def next_policy(self, current: AgentProfile) -> AgentProfile:
        order = self.get_policy_order()
        idx = order.index(current.name) if current.name in order else -1
        return self.available_policies[order[(idx + 1) % len(order)]]

    def under(self, orchestrator: ConfigOrchestrator[VibeConfigSchema]) -> AgentManager:
        """The same agents read against ``orchestrator``, a candidate config:
        switching there touches neither this manager nor its config.
        """
        view = copy.copy(self)
        view._orchestrator = orchestrator
        view._forced_policies = set(self._forced_policies)
        view.restore_warnings = list(self.restore_warnings)
        return view

    def switch_agent(self, name: str) -> None:
        """Off the event loop only; code on it uses ``aswitch_agent``."""
        agent = self.get_agent(name)
        self._install(agent, self.active_policy)
        self.active_agent = agent

    def switch_policy(self, name: str) -> None:
        """Off the event loop only; code on it uses ``aswitch_policy``."""
        policy = self.get_policy(name)
        self._install(self.active_agent, policy)
        self.active_policy = policy

    async def aswitch_agent(self, name: str) -> None:
        """``switch_agent``, rebuilding the config on the event loop: a
        synchronous rebuild there deadlocks against a reload in flight.
        """
        agent = self.get_agent(name)
        self._check_installable(agent)
        await apply_session_layers_async(self._orchestrator, agent, self.active_policy)
        self.active_agent = agent

    async def aswitch_policy(self, name: str) -> None:
        policy = self.get_policy(name)
        self._check_installable(policy)
        await apply_session_layers_async(self._orchestrator, self.active_agent, policy)
        self.active_policy = policy

    async def areapply(self) -> None:
        """Reinstall the running agent and policy over a changed config."""
        await apply_session_layers_async(
            self._orchestrator, self.active_agent, self.active_policy
        )

    def _install(self, agent: AgentProfile, policy: AgentProfile) -> None:
        self._check_installable(agent)
        self._check_installable(policy)
        apply_session_layers(self._orchestrator, agent, policy)

    def _check_installable(self, profile: AgentProfile) -> None:
        if profile.config_error is not None:
            raise InvalidConfigError(
                f"Agent '{profile.name}' in {profile.source_path}:"
                f" {profile.config_error}"
            )
        refuse_profile_shell_timeouts_over_the_users(self._orchestrator, profile)


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
