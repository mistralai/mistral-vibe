from __future__ import annotations

from collections.abc import Callable, Mapping

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
    AppServerResponseError,
    ConfigFieldsReadParams,
    ConfigFieldsReadResponse,
    ConfigMutationResponse,
    ConfigPatchOpWire,
    ConfigPatchParams,
    ConfigPatchResponse,
    ConfigProxyReadParams,
    ConfigProxyReadResponse,
    ConfigProxyWriteParams,
    ConfigReadParams,
    ConfigReadResponse,
    ConfigReloadParams,
    ConfigSchemaReadParams,
    ConfigSchemaReadResponse,
    ConfigThinkingWriteParams,
    DiagnosticsLogsReadParams,
    DiagnosticsLogsReadResponse,
    EmptyResponse,
    Notification,
    ProtocolError,
    ProtocolErrorCode,
    RuntimeMutationResponse,
    RuntimeReadParams,
    RuntimeReadResponse,
    RuntimeSnapshot,
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
        self._subscribers: list[Callable[[ConfigView], None]] = []

    @property
    def current(self) -> ConfigView:
        return self._state.config

    @property
    def base(self) -> ConfigView:
        return self._state.base_config

    def subscribe(self, callback: Callable[[ConfigView], None]) -> Callable[[], None]:
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return unsubscribe

    def publish_change(self, previous: ConfigView) -> None:
        if previous == self.current:
            return
        for callback in list(self._subscribers):
            callback(self.current)

    def _apply_runtime(self, snapshot: RuntimeSnapshot) -> None:
        previous = self.current
        self._state.apply_runtime(snapshot)
        self.publish_change(previous)

    async def read(self) -> None:
        client = await self._connection.connect()
        response = validate_wire(
            ConfigReadResponse,
            await client.request(
                "config/read", ConfigReadParams(session_id=self._state.session_id)
            ),
        )
        previous = self.current
        self._state.apply_config(response)
        self.publish_change(previous)

    async def read_schema(self) -> ConfigSchemaReadResponse:
        client = await self._connection.connect()
        return validate_wire(
            ConfigSchemaReadResponse,
            await client.request("config/schema", ConfigSchemaReadParams()),
        )

    async def read_fields(self) -> ConfigFieldsReadResponse:
        client = await self._connection.connect()
        return validate_wire(
            ConfigFieldsReadResponse,
            await client.request(
                "config/fields/read",
                ConfigFieldsReadParams(session_id=self._state.session_id),
            ),
        )

    async def patch(
        self, ops: list[ConfigPatchOpWire], *, reason: str
    ) -> ConfigPatchResponse:
        client = await self._connection.connect()
        response = validate_wire(
            ConfigPatchResponse,
            await client.request(
                "config/patch",
                ConfigPatchParams(
                    session_id=self._state.session_id, ops=ops, reason=reason
                ),
            ),
        )
        if not response.rejected and not response.failures:
            self._apply_runtime(response.runtime)
        return response

    async def update(
        self, changes: Mapping[str, object], *, reload_runtime: bool = False
    ) -> None:
        ops = [
            ConfigPatchOpWire.model_validate({
                "op": "set",
                "path": f"/{key}",
                "value": value,
            })
            for key, value in changes.items()
        ]
        client = await self._connection.connect()
        response = validate_wire(
            ConfigPatchResponse,
            await client.request(
                "config/patch",
                ConfigPatchParams(
                    session_id=self._state.session_id,
                    ops=ops,
                    reason="app-server config update",
                    reload_runtime=reload_runtime,
                ),
            ),
        )
        if response.rejected:
            raise AppServerResponseError(
                ProtocolError(
                    code=ProtocolErrorCode.INVALID_PARAMS,
                    message="Invalid configuration edit",
                )
            )
        if response.failures:
            raise AppServerResponseError(
                ProtocolError(
                    code=ProtocolErrorCode.INTERNAL_ERROR,
                    message="; ".join(response.failures),
                )
            )
        self._apply_runtime(response.runtime)

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
        self._apply_runtime(response.runtime)

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
        self._apply_runtime(response.runtime)
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
