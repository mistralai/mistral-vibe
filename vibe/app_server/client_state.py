from __future__ import annotations

from dataclasses import dataclass

from vibe.app_server.events import ClientProjection
from vibe.app_server.models import (
    AgentSummary,
    AgentType,
    ApprovalPolicySummary,
    PublicSessionState,
    SkillSummary,
    TokenUsage,
)
from vibe.app_server.protocol import (
    AgentsListResponse,
    RuntimeReadResponse,
    RuntimeSnapshot,
)


@dataclass(frozen=True, slots=True)
class ClientBootstrap:
    state: PublicSessionState
    runtime: RuntimeReadResponse


class ClientSessionState:
    def __init__(self, bootstrap: ClientBootstrap) -> None:
        self.projection = ClientProjection(bootstrap.state)
        self.apply_runtime_read(bootstrap.runtime)
        self._usage_baseline = self._current_usage()

    @property
    def session_id(self) -> str:
        return self.projection.state.session.id

    @property
    def state(self) -> PublicSessionState:
        return self.projection.state

    @property
    def custom_skills_count(self) -> int:
        return sum(
            skill.source != "builtin" and skill.scope != "builtin"
            for skill in self.skills
        )

    def get_skill(self, name: str) -> SkillSummary | None:
        folded = name.casefold()
        return next(
            (skill for skill in self.skills if skill.name.casefold() == folded), None
        )

    def has_tool(self, name: str) -> bool:
        return any(tool.name == name for tool in self.tools)

    def next_policy(self, current_name: str | None = None) -> ApprovalPolicySummary:
        if not self.approval_policies:
            raise RuntimeError("The app server returned no approval policies")
        current = current_name or self.active_approval_policy.name
        index = next(
            (
                index
                for index, policy in enumerate(self.approval_policies)
                if policy.name == current
            ),
            -1,
        )
        return self.approval_policies[(index + 1) % len(self.approval_policies)]

    def primary_agents(self) -> list[AgentSummary]:
        return [agent for agent in self.agents if agent.agent_type is AgentType.AGENT]

    def apply_agents(self, response: AgentsListResponse) -> None:
        self.active_agent = response.active
        self.agents = list(response.agents)
        self.active_approval_policy = response.active_approval_policy
        self.approval_policies = list(response.approval_policies)
        self.state.session.agent = response.active
        self.state.session.approval_policy = response.active_approval_policy

    def apply_runtime(self, snapshot: RuntimeSnapshot) -> None:
        self.config = snapshot.config
        self.active_agent = snapshot.active_agent
        self.agents = list(snapshot.agents)
        self.active_approval_policy = snapshot.active_approval_policy
        self.approval_policies = list(snapshot.approval_policies)
        self.skills = list(snapshot.skills)
        self.tools = list(snapshot.tools)
        self.stats = snapshot.stats
        self.context_window = snapshot.context_window
        self.issues = list(snapshot.issues)
        self.hooks_count = snapshot.hooks_count
        self.connectors = snapshot.connectors
        # The Studio "add connectors" link and the private-connectors warning
        # are resolved on the client during connector_catalog/read and never
        # ride the runtime snapshot, so a mutation snapshot (toggle/refresh)
        # would blank them. Carry them forward until the next catalog read.
        previous_mcp = getattr(self, "mcp", None)
        carried_manage_url = (
            previous_mcp.manage_connectors_url if previous_mcp is not None else None
        )
        carried_private_hidden = (
            previous_mcp is not None and previous_mcp.private_connectors_hidden
        )
        self.mcp = snapshot.mcp.model_copy(
            update={
                "manage_connectors_url": snapshot.mcp.manage_connectors_url
                if snapshot.mcp.manage_connectors_url is not None
                else carried_manage_url,
                "private_connectors_hidden": snapshot.mcp.private_connectors_hidden
                or carried_private_hidden,
            }
        )
        self.bypass_tool_permissions = snapshot.bypass_tool_permissions
        self.state.session.model = snapshot.config.active_model.alias
        self.state.session.agent = snapshot.active_agent
        self.state.session.approval_policy = snapshot.active_approval_policy
        self.state.session.token_usage = snapshot.stats.token_usage

    def apply_runtime_read(self, response: RuntimeReadResponse) -> None:
        self.apply_runtime(response.runtime)
        self.session_log = response.session_log
        self.ready = response.ready

    def reset_usage_baseline(self) -> None:
        self._usage_baseline = self._current_usage()

    def usage_since_baseline(self) -> TokenUsage:
        current = self._current_usage()
        input_tokens = max(0, current.input_tokens - self._usage_baseline.input_tokens)
        output_tokens = max(
            0, current.output_tokens - self._usage_baseline.output_tokens
        )
        cached_input_tokens = max(
            0, current.cached_input_tokens - self._usage_baseline.cached_input_tokens
        )
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cached_input_tokens=cached_input_tokens,
        )

    def _current_usage(self) -> TokenUsage:
        return self.state.session.token_usage or self.stats.token_usage
