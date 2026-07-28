from __future__ import annotations

from dataclasses import dataclass

from vibe.app_server.events import ClientProjection
from vibe.app_server.models import (
    AgentSummary,
    AgentType,
    PublicSessionState,
    SkillSummary,
    TokenUsage,
)
from vibe.app_server.protocol import (
    AgentsListResponse,
    ConfigReadResponse,
    DiagnosticsListResponse,
    RuntimeReadResponse,
    RuntimeSnapshot,
    StatsReadResponse,
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
        return sum(skill.source != "builtin" for skill in self.skills)

    def get_skill(self, name: str) -> SkillSummary | None:
        return next((skill for skill in self.skills if skill.name == name), None)

    def has_tool(self, name: str) -> bool:
        return any(tool.name == name for tool in self.tools)

    def next_agent(self, current_name: str | None = None) -> AgentSummary:
        primary = [
            agent for agent in self.agents if agent.agent_type is AgentType.AGENT
        ]
        if not primary:
            raise RuntimeError("The app server returned no primary agents")
        current = current_name or self.active_agent.name
        index = next(
            (index for index, agent in enumerate(primary) if agent.name == current), -1
        )
        return primary[(index + 1) % len(primary)]

    def apply_config(self, response: ConfigReadResponse) -> None:
        self.config = response.config
        self.base_config = response.base_config
        self.state.session.model = response.config.active_model.alias

    def apply_agents(self, response: AgentsListResponse) -> None:
        self.active_agent = response.active
        self.agents = list(response.agents)
        self.state.session.agent = response.active

    def apply_stats(self, response: StatsReadResponse) -> None:
        self.stats = response.stats
        self.context_window = response.context_window
        self.state.session.token_usage = response.stats.token_usage

    def apply_diagnostics(self, response: DiagnosticsListResponse) -> None:
        self.issues = list(response.issues)
        self.hooks_count = response.hooks_count

    def apply_runtime(self, snapshot: RuntimeSnapshot) -> None:
        self.config = snapshot.config
        self.base_config = snapshot.base_config
        self.active_agent = snapshot.active_agent
        self.agents = list(snapshot.agents)
        self.skills = list(snapshot.skills)
        self.tools = list(snapshot.tools)
        self.stats = snapshot.stats
        self.context_window = snapshot.context_window
        self.issues = list(snapshot.issues)
        self.hooks_count = snapshot.hooks_count
        self.connectors = snapshot.connectors
        self.mcp = snapshot.mcp
        self.state.session.model = snapshot.config.active_model.alias
        self.state.session.agent = snapshot.active_agent
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
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )

    def _current_usage(self) -> TokenUsage:
        return self.state.session.token_usage or self.stats.token_usage
