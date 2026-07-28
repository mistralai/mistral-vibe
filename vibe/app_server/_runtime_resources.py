from __future__ import annotations

from collections.abc import Mapping

from vibe.app_server._model import validate_wire
from vibe.app_server.client_state import ClientSessionState
from vibe.app_server.config import ConfigView, ProxySettingsView, ThinkingLevel
from vibe.app_server.connection import AppServerResourceConnection
from vibe.app_server.models import (
    AccountView,
    AgentStatsSnapshot,
    AgentSummary,
    ConfigIssue,
    ConnectorCounts,
    DebugLogPage,
    MCPState,
    SessionLogSummary,
    SkillSummary,
    ToolSummary,
)
from vibe.app_server.protocol import (
    AccountReadParams,
    AccountReadResponse,
    AgentInstallParams,
    AgentsListParams,
    AgentsListResponse,
    AgentSwitchParams,
    ConfigEdit,
    ConfigMutationResponse,
    ConfigProxyReadParams,
    ConfigProxyReadResponse,
    ConfigProxyWriteParams,
    ConfigReadParams,
    ConfigReadResponse,
    ConfigReloadParams,
    ConfigSchemaReadParams,
    ConfigSchemaReadResponse,
    ConfigThinkingWriteParams,
    ConfigWriteParams,
    DiagnosticsLogsReadParams,
    DiagnosticsLogsReadResponse,
    EmptyResponse,
    Notification,
    RuntimeMutationResponse,
    RuntimeReadParams,
    RuntimeReadResponse,
    RuntimeUpdatedParams,
    SessionReadyWaitParams,
    SessionReadyWaitResponse,
)


class ConfigResource:
    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state

    @property
    def current(self) -> ConfigView:
        return self._state.config

    @property
    def base(self) -> ConfigView:
        return self._state.base_config

    async def read(self) -> None:
        client = await self._connection.connect()
        response = validate_wire(
            ConfigReadResponse,
            await client.request(
                "config/read", ConfigReadParams(session_id=self._state.session_id)
            ),
        )
        self._state.apply_config(response)

    async def read_schema(self) -> ConfigSchemaReadResponse:
        client = await self._connection.connect()
        return validate_wire(
            ConfigSchemaReadResponse,
            await client.request("config/schema", ConfigSchemaReadParams()),
        )

    async def update(
        self, changes: Mapping[str, object], *, reload_runtime: bool = False
    ) -> None:
        client = await self._connection.connect()
        response = validate_wire(
            ConfigMutationResponse,
            await client.request(
                "config/batchWrite",
                ConfigWriteParams(
                    session_id=self._state.session_id,
                    edits=[
                        ConfigEdit.model_validate({"path": f"/{key}", "value": value})
                        for key, value in changes.items()
                    ],
                    reload_runtime=reload_runtime,
                ),
            ),
        )
        self._state.apply_runtime(response.runtime)

    async def set_thinking(self, level: ThinkingLevel) -> None:
        client = await self._connection.connect()
        response = validate_wire(
            ConfigMutationResponse,
            await client.request(
                "config/thinking/write",
                ConfigThinkingWriteParams(
                    session_id=self._state.session_id, level=level
                ),
            ),
        )
        self._state.apply_runtime(response.runtime)

    async def reload(self, *, reload_runtime: bool = True) -> int:
        client = await self._connection.connect()
        response = validate_wire(
            ConfigMutationResponse,
            await client.request(
                "config/reload",
                ConfigReloadParams(
                    session_id=self._state.session_id, reload_runtime=reload_runtime
                ),
            ),
        )
        self._state.apply_runtime(response.runtime)
        return response.stripped_history_images

    async def read_proxy(self) -> ProxySettingsView:
        client = await self._connection.connect()
        response = validate_wire(
            ConfigProxyReadResponse,
            await client.request(
                "config/proxy/read",
                ConfigProxyReadParams(session_id=self._state.session_id),
            ),
        )
        return response.settings

    async def update_proxy(self, changes: Mapping[str, str | None]) -> None:
        client = await self._connection.connect()
        validate_wire(
            EmptyResponse,
            await client.request(
                "config/proxy/write",
                ConfigProxyWriteParams(
                    session_id=self._state.session_id, changes=dict(changes)
                ),
            ),
        )


class AccountResource:
    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state
        self._current: AccountView | None = None

    @property
    def current(self) -> AccountView | None:
        return self._current

    async def read(self) -> AccountView:
        self._current = None
        client = await self._connection.connect()
        response = validate_wire(
            AccountReadResponse,
            await client.request(
                "account/read", AccountReadParams(session_id=self._state.session_id)
            ),
        )
        self._current = response.account
        return response.account


class AgentResource:
    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state

    @property
    def active(self) -> AgentSummary:
        return self._state.active_agent

    @property
    def all(self) -> list[AgentSummary]:
        return self._state.agents

    def next(self, current_name: str | None = None) -> AgentSummary:
        return self._state.next_agent(current_name)

    async def read(self) -> None:
        client = await self._connection.connect()
        response = validate_wire(
            AgentsListResponse,
            await client.request(
                "agents/list", AgentsListParams(session_id=self._state.session_id)
            ),
        )
        self._state.apply_agents(response)

    async def switch(self, agent_name: str) -> AgentSummary:
        client = await self._connection.connect()
        response = validate_wire(
            RuntimeMutationResponse,
            await client.request(
                "session/agent/update",
                AgentSwitchParams(
                    session_id=self._state.session_id, agent_name=agent_name
                ),
            ),
        )
        self._state.apply_runtime(response.runtime)
        return response.runtime.active_agent

    async def set_installed(self, agent_name: str, *, installed: bool) -> None:
        client = await self._connection.connect()
        method = "agents/install" if installed else "agents/uninstall"
        response = validate_wire(
            AgentsListResponse,
            await client.request(
                method,
                AgentInstallParams(
                    session_id=self._state.session_id, agent_name=agent_name
                ),
            ),
        )
        self._state.apply_agents(response)


class RuntimeResource:
    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state

    @property
    def skills(self) -> list[SkillSummary]:
        return self._state.skills

    @property
    def tools(self) -> list[ToolSummary]:
        return self._state.tools

    @property
    def stats(self) -> AgentStatsSnapshot:
        return self._state.stats

    @property
    def context_window(self) -> int:
        return self._state.context_window

    @property
    def issues(self) -> list[ConfigIssue]:
        return self._state.issues

    @property
    def hooks_count(self) -> int:
        return self._state.hooks_count

    @property
    def connectors(self) -> ConnectorCounts:
        return self._state.connectors

    @property
    def mcp(self) -> MCPState:
        return self._state.mcp

    @property
    def session_log(self) -> SessionLogSummary:
        return self._state.session_log

    @property
    def ready(self) -> bool:
        return self._state.ready

    @property
    def custom_skills_count(self) -> int:
        return self._state.custom_skills_count

    def get_skill(self, name: str) -> SkillSummary | None:
        return self._state.get_skill(name)

    def has_tool(self, name: str) -> bool:
        return self._state.has_tool(name)

    async def read_logs(self, *, limit: int = 100, offset: int = 0) -> DebugLogPage:
        client = await self._connection.connect()
        response = validate_wire(
            DiagnosticsLogsReadResponse,
            await client.request(
                "diagnostics/logs/read",
                DiagnosticsLogsReadParams(
                    session_id=self._state.session_id, limit=limit, offset=offset
                ),
            ),
        )
        return response.logs

    async def wait_until_ready(self) -> None:
        client = await self._connection.connect()
        validate_wire(
            SessionReadyWaitResponse,
            await client.request(
                "session/ready/wait",
                SessionReadyWaitParams(session_id=self._state.session_id),
            ),
        )
        self._state.ready = True
        await self.refresh()

    async def refresh(self) -> None:
        client = await self._connection.connect()
        response = validate_wire(
            RuntimeReadResponse,
            await client.request(
                "runtime/read", RuntimeReadParams(session_id=self._state.session_id)
            ),
        )
        self._state.apply_runtime_read(response)

    async def consume_notification(self, notification: Notification) -> bool:
        if notification.method != "runtime/updated":
            return False
        params = validate_wire(RuntimeUpdatedParams, notification.params)
        if params.session_id != self._state.session_id:
            return False
        self._state.apply_runtime(params.runtime)
        return True
