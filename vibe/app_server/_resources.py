from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast

from pydantic import JsonValue

from vibe.app_server._account import AccountController, AccountGateway
from vibe.app_server._admin_config import (
    refresh_admin_layer,
    report_admin_config_outcome,
)
from vibe.app_server._config_introspect import (
    HIDDEN_SETTINGS,
    POPULAR_SETTINGS,
    build_field_wires,
    collect_layer_values,
)
from vibe.app_server._config_write import config_write_ops_to_patches
from vibe.app_server._dispatch import DispatchResult, RequestFailure, method_not_found
from vibe.app_server._execution import SessionExecution
from vibe.app_server._identity import IdentityController, IdentityGateway
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._narration import NarrationService
from vibe.app_server._projection import (
    project_agents,
    project_config,
    project_connectors,
    project_debug_logs,
    project_diagnostics,
    project_mcp,
    project_session_log,
    project_skills,
    project_stats,
    project_tools,
)
from vibe.app_server.config import ProxySettingsView
from vibe.app_server.models import AccountView, IdentityView, MCPState, ScheduledLoop
from vibe.app_server.protocol import (
    AccountReadParams,
    AccountReadResponse,
    AgentInstallParams,
    AgentsListParams,
    AgentsListResponse,
    ConfigFieldsReadParams,
    ConfigFieldsReadResponse,
    ConfigMutationResponse,
    ConfigProxyReadParams,
    ConfigProxyReadResponse,
    ConfigProxyWriteParams,
    ConfigReadParams,
    ConfigReadResponse,
    ConfigReloadParams,
    ConfigWriteOpWire,
    ConfigWriteParams,
    ConfigWriteResponse,
    ConnectorAuthReadParams,
    ConnectorAuthReadResponse,
    ConnectorRefreshParams,
    ConnectorRefreshResponse,
    ConnectorsReadParams,
    ConnectorsReadResponse,
    DiagnosticsListParams,
    DiagnosticsListResponse,
    DiagnosticsLogsReadParams,
    DiagnosticsLogsReadResponse,
    EmptyResponse,
    FeedbackRecordParams,
    FeedbackShouldShowParams,
    FeedbackShouldShowResponse,
    IdentityReadParams,
    IdentityReadResponse,
    LoopsClearParams,
    LoopsClearResponse,
    LoopsCreateParams,
    LoopsCreateResponse,
    LoopsDeleteParams,
    LoopsDeleteResponse,
    LoopsListParams,
    LoopsListResponse,
    NarrationSummarizeParams,
    NarrationSummarizeResponse,
    ProtocolErrorCode,
    RuntimeReadParams,
    RuntimeReadResponse,
    RuntimeSnapshot,
    SkillsListParams,
    SkillsListResponse,
    StatsReadParams,
    StatsReadResponse,
    TelemetryRecordParams,
    ToolsListParams,
    ToolsListResponse,
)
from vibe.core.agent_loop import AgentLoop
from vibe.core.config.admin_config import (
    MANAGED_CONFIG_TIMEOUT,
    AdminConfigApplyResult,
    AdminConfigOutcome,
)
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.orchestrator import ConfigPatchValidationError
from vibe.core.feedback import (
    record_feedback_asked,
    record_feedback_given,
    record_feedback_snoozed,
    should_show_feedback,
)
from vibe.core.log_reader import LogReader
from vibe.core.loop import LoopError, LoopManager
from vibe.core.proxy_setup import (
    SUPPORTED_PROXY_VARS,
    ProxySetupError,
    get_current_proxy_settings,
    set_proxy_var,
    unset_proxy_var,
)
from vibe.core.types import Role, ScheduledLoop as CoreScheduledLoop
from vibe.observability.logging import logger


class ResourceRequestHandler:
    def __init__(
        self,
        agent_loop: AgentLoop,
        execution: SessionExecution,
        notify: Callable[[str, ProtocolModel], Awaitable[None]],
        account_gateway: AccountGateway | None = None,
        current_event_id: Callable[[str], int] | None = None,
        identity_gateway: IdentityGateway | None = None,
    ) -> None:
        self._agent_loop = agent_loop
        self._execution = execution
        self._notify = notify
        self._current_event_id = current_event_id or (lambda _session_id: 0)
        self._account = AccountController(agent_loop, account_gateway)
        self._identity = IdentityController(agent_loop, identity_gateway)
        self._loops = LoopManager(agent_loop.session_logger)
        self._logs = LogReader()
        self._narration = NarrationService(agent_loop)
        self._mcp_discovery_errors: dict[str, str] = {}
        self.restore_loops()

    async def dispatch(self, method: str, raw_params: dict[str, Any]) -> DispatchResult:
        namespace = method.partition("/")[0]
        match namespace:
            case "runtime":
                result = self._dispatch_runtime(method, raw_params)
            case "account":
                result = await self._dispatch_account(method, raw_params)
            case "identity":
                result = await self._dispatch_identity(method, raw_params)
            case "config":
                result = await self._dispatch_config(method, raw_params)
            case "agents":
                result = await self._dispatch_agents(method, raw_params)
            case "skills" | "tools" | "stats" | "diagnostics":
                result = self._dispatch_catalog(method, raw_params)
            case "connectors":
                result = await self._dispatch_connectors(method, raw_params)
            case "loops":
                result = await self._dispatch_loops(method, raw_params)
            case "narration":
                result = await self._dispatch_narration(method, raw_params)
            case "telemetry" | "feedback":
                result = self._dispatch_client_event(method, raw_params)
            case _:
                raise method_not_found(method)
        return result

    def _dispatch_runtime(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method != "runtime/read":
            raise method_not_found(method)
        params = validate_wire(RuntimeReadParams, raw_params)
        self._require_session(params.session_id)
        return DispatchResult(
            RuntimeReadResponse(
                runtime=self.runtime_snapshot(),
                session_log=project_session_log(self._agent_loop),
                ready=self._agent_loop.is_initialized,
            )
        )

    def restore_loops(self) -> None:
        metadata = self._agent_loop.session_logger.session_metadata
        self._loops.restore(list(metadata.loops) if metadata is not None else [])

    def transfer_loops(self) -> None:
        metadata = self._agent_loop.session_logger.session_metadata
        if metadata is not None:
            metadata.loops = self._loops.loops

    def next_loop_due_in(self) -> float:
        return self._loops.next_due_in()

    async def read_account(self) -> AccountView:
        return await self._account.read()

    async def read_identity(self) -> IdentityView | None:
        return await self._identity.read()

    def due_loop(self) -> CoreScheduledLoop | None:
        return self._loops.due()

    async def mark_loop_fired(self, loop_id: str) -> None:
        await self._loops.mark_fired(loop_id)

    def runtime_snapshot(self) -> RuntimeSnapshot:
        active, agents = project_agents(self._agent_loop)
        issues, hooks_count = project_diagnostics(self._agent_loop)
        return RuntimeSnapshot(
            config=project_config(self._agent_loop),
            active_agent=active,
            agents=agents,
            skills=project_skills(self._agent_loop),
            tools=project_tools(self._agent_loop),
            stats=project_stats(self._agent_loop),
            context_window=self._context_window(),
            issues=issues,
            hooks_count=hooks_count,
            connectors=project_connectors(self._agent_loop),
            mcp=self._mcp_state(),
        )

    def _mcp_state(self) -> MCPState:
        self._mcp_discovery_errors.update(
            self._agent_loop.tool_manager.pop_mcp_errors()
        )
        return project_mcp(
            self._agent_loop, discovery_errors=self._mcp_discovery_errors
        )

    def _clear_mcp_discovery_errors(self) -> None:
        self._mcp_discovery_errors.clear()

    async def _dispatch_account(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method != "account/read":
            raise method_not_found(method)
        params = validate_wire(AccountReadParams, raw_params)
        self._require_session(params.session_id)
        return DispatchResult(AccountReadResponse(account=await self.read_account()))

    async def _dispatch_identity(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method != "identity/read":
            raise method_not_found(method)
        params = validate_wire(IdentityReadParams, raw_params)
        self._require_session(params.session_id)
        return DispatchResult(IdentityReadResponse(identity=await self.read_identity()))

    async def _dispatch_config(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        match method:
            case "config/read":
                response: ProtocolModel = self._config_read(
                    validate_wire(ConfigReadParams, raw_params)
                )
                runtime_updated = False
            case "config/write":
                write_response = await self._config_write(
                    validate_wire(ConfigWriteParams, raw_params)
                )
                response = write_response
                runtime_updated = not write_response.rejected and not (
                    write_response.failures
                )
            case "config/fields/read":
                response = await self._config_fields_read(
                    validate_wire(ConfigFieldsReadParams, raw_params)
                )
                runtime_updated = False
            case "config/reload":
                response = await self._config_reload(
                    validate_wire(ConfigReloadParams, raw_params)
                )
                runtime_updated = True
            case "config/proxy/read":
                response = await self._config_proxy_read(
                    validate_wire(ConfigProxyReadParams, raw_params)
                )
                runtime_updated = False
            case "config/proxy/write":
                response = await self._config_proxy_write(
                    validate_wire(ConfigProxyWriteParams, raw_params)
                )
                runtime_updated = False
            case _:
                raise method_not_found(method)
        return DispatchResult(response, runtime_updated=runtime_updated)

    async def _dispatch_narration(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method != "narration/summarize":
            raise method_not_found(method)
        params = validate_wire(NarrationSummarizeParams, raw_params)
        self._require_session(params.session_id)
        summary = await self._narration.summarize(params)
        return DispatchResult(NarrationSummarizeResponse(summary=summary))

    async def _dispatch_agents(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        match method:
            case "agents/list":
                response: ProtocolModel = self._agents_list(
                    validate_wire(AgentsListParams, raw_params)
                )
                runtime_updated = False
            case "agents/install":
                response = await self._agent_install(
                    validate_wire(AgentInstallParams, raw_params), install=True
                )
                runtime_updated = True
            case "agents/uninstall":
                response = await self._agent_install(
                    validate_wire(AgentInstallParams, raw_params), install=False
                )
                runtime_updated = True
            case _:
                raise method_not_found(method)
        return DispatchResult(response, runtime_updated=runtime_updated)

    def _dispatch_catalog(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        match method:
            case "skills/list":
                response: ProtocolModel = self._skills_list(
                    validate_wire(SkillsListParams, raw_params)
                )
            case "tools/list":
                response = self._tools_list(validate_wire(ToolsListParams, raw_params))
            case "stats/read":
                response = self._stats_read(validate_wire(StatsReadParams, raw_params))
            case "diagnostics/list":
                response = self._diagnostics_list(
                    validate_wire(DiagnosticsListParams, raw_params)
                )
            case "diagnostics/logs/read":
                response = self._diagnostics_logs_read(
                    validate_wire(DiagnosticsLogsReadParams, raw_params)
                )
            case _:
                raise method_not_found(method)
        return DispatchResult(response)

    async def _dispatch_connectors(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        match method:
            case "connectors/read":
                response: ProtocolModel = self._connectors_read(
                    validate_wire(ConnectorsReadParams, raw_params)
                )
                runtime_updated = False
            case "connectors/auth/read":
                response = await self._connector_auth_read(
                    validate_wire(ConnectorAuthReadParams, raw_params)
                )
                runtime_updated = False
            case "connectors/refresh":
                response = await self._connector_refresh(
                    validate_wire(ConnectorRefreshParams, raw_params)
                )
                runtime_updated = True
            case _:
                raise method_not_found(method)
        return DispatchResult(response, runtime_updated=runtime_updated)

    async def _dispatch_loops(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        try:
            match method:
                case "loops/list":
                    params = validate_wire(LoopsListParams, raw_params)
                    self._require_session(params.session_id)
                    response: ProtocolModel = LoopsListResponse(
                        loops=[_project_loop(loop) for loop in self._loops.loops]
                    )
                case "loops/create":
                    self._execution.require_idle()
                    params = validate_wire(LoopsCreateParams, raw_params)
                    self._require_session(params.session_id)
                    response = LoopsCreateResponse(
                        loop=_project_loop(
                            await self._loops.create(params.interval, params.prompt)
                        )
                    )
                case "loops/delete":
                    self._execution.require_idle()
                    params = validate_wire(LoopsDeleteParams, raw_params)
                    self._require_session(params.session_id)
                    response = LoopsDeleteResponse(
                        loop=_project_loop(await self._loops.delete(params.loop_id))
                    )
                case "loops/clear":
                    self._execution.require_idle()
                    params = validate_wire(LoopsClearParams, raw_params)
                    self._require_session(params.session_id)
                    response = LoopsClearResponse(count=await self._loops.clear())
                case _:
                    raise method_not_found(method)
        except LoopError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        return DispatchResult(response)

    def _dispatch_client_event(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        if method == "telemetry/record":
            return self._dispatch_telemetry(raw_params)
        return self._dispatch_feedback(method, raw_params)

    def _dispatch_telemetry(self, raw_params: dict[str, Any]) -> DispatchResult:
        params = validate_wire(TelemetryRecordParams, raw_params)
        self._require_session(params.session_id)
        client = self._agent_loop.telemetry_client
        client.send_telemetry_event(
            params.name,
            params.properties,
            correlation_id=(
                client.last_correlation_id if params.correlate_last_request else None
            ),
        )
        return DispatchResult(EmptyResponse())

    def _dispatch_feedback(
        self, method: str, raw_params: dict[str, Any]
    ) -> DispatchResult:
        match method:
            case "feedback/shouldShow":
                params = validate_wire(FeedbackShouldShowParams, raw_params)
                self._require_session(params.session_id)
                user_messages = sum(
                    message.role is Role.user and not message.injected
                    for message in self._agent_loop.messages
                )
                response: ProtocolModel = FeedbackShouldShowResponse(
                    show=should_show_feedback(
                        telemetry_active=self._agent_loop.telemetry_client.is_active(),
                        is_mistral_model=(
                            self._agent_loop.config.is_active_model_mistral()
                        ),
                        user_message_count=(
                            user_messages + params.pending_user_messages
                        ),
                        cache_store=self._agent_loop.cache_store,
                    )
                )
            case "feedback/record":
                params = validate_wire(FeedbackRecordParams, raw_params)
                self._require_session(params.session_id)
                match params.action:
                    case "asked":
                        record_feedback_asked(self._agent_loop.cache_store)
                    case "given":
                        record_feedback_given(self._agent_loop.cache_store)
                    case "snoozed":
                        record_feedback_snoozed(self._agent_loop.cache_store)
                response = EmptyResponse()
            case _:
                raise method_not_found(method)
        return DispatchResult(response)

    def _config_read(self, params: ConfigReadParams) -> ConfigReadResponse:
        if params.session_id is not None:
            self._require_session(params.session_id)
        config = project_config(self._agent_loop)
        skills_count = sum(
            1 for skill in project_skills(self._agent_loop) if skill.source != "builtin"
        )
        _, hooks_count = project_diagnostics(self._agent_loop)
        mcp_servers_total = len(self._agent_loop.config.mcp_servers)
        mcp_servers_enabled = sum(
            1 for server in self._agent_loop.config.mcp_servers if not server.disabled
        )
        return ConfigReadResponse(
            config=config,
            skills_count=skills_count,
            hooks_count=hooks_count,
            mcp_servers_total=mcp_servers_total,
            mcp_servers_enabled=mcp_servers_enabled,
        )

    async def _config_write(self, params: ConfigWriteParams) -> ConfigWriteResponse:
        self._execution.require_idle()
        self._require_session(params.session_id)
        durable_aliases = (
            await self._agent_loop.config_orchestrator.durable_model_aliases()
        )
        operations = config_write_ops_to_patches(
            self._agent_loop.config, params.ops, durable_model_aliases=durable_aliases
        )
        try:
            failures = await self._agent_loop.config_orchestrator.apply_patch(
                operations, reason=params.reason
            )
        except (ConfigPatchValidationError, ValueError):
            return ConfigWriteResponse(runtime=self.runtime_snapshot(), rejected=True)
        if failures:
            return ConfigWriteResponse(
                runtime=self.runtime_snapshot(),
                failures=[str(failure) for failure in failures],
            )
        if params.reload_runtime:
            self._clear_mcp_discovery_errors()
            await self._agent_loop.reload_with_initial_messages(reload_hooks=True)
        return ConfigWriteResponse(
            runtime=self.runtime_snapshot(),
            stripped_history_images=(
                self._agent_loop.count_history_images_unsupported_by_active_model()
            ),
        )

    async def _config_reload(
        self, params: ConfigReloadParams
    ) -> ConfigMutationResponse:
        self._execution.require_idle()
        self._require_session(params.session_id)
        # Best-effort: an admin-fetch failure must never break the user's reload.
        # asyncio.timeout caps the full retry budget so /reload stays responsive;
        # startup still uses the uncapped retry policy via apply_admin_config().
        try:
            async with asyncio.timeout(MANAGED_CONFIG_TIMEOUT * 1.5):
                self._report_admin_config_outcome(await self._refresh_admin_layer())
        except Exception as exc:
            logger.debug("Admin config refresh failed on reload", exc_info=exc)
        if params.reload_runtime:
            self._clear_mcp_discovery_errors()
            await self._agent_loop.config_orchestrator.reload()
            await self._agent_loop.reload_with_initial_messages(reload_hooks=True)
        else:
            await self._agent_loop.refresh_config()
        return self._config_mutation_response()

    async def apply_admin_config(self) -> bool:
        """Pull org-enforced config and merge it as the highest-priority layer.

        Runs only when a Mistral API key is in use. Any failure is silent: the
        admin layer stays empty and has no impact on the client. Returns whether
        the effective config changed, so the caller can push a runtime update.
        """
        result = await self._refresh_admin_layer()
        if not result.applied:
            self._report_admin_config_outcome(result)
            return False
        try:
            await self._agent_loop.refresh_config()
        except Exception as exc:
            logger.warning("Failed to apply admin-managed config", exc_info=exc)
            self._agent_loop.telemetry_client.send_admin_config_applied(
                outcome=AdminConfigOutcome.APPLY_FAILED, error=str(exc)
            )
            return False
        self._report_admin_config_outcome(result)
        return True

    def _report_admin_config_outcome(self, result: AdminConfigApplyResult) -> None:
        report_admin_config_outcome(result, telemetry=self._agent_loop.telemetry_client)

    async def _refresh_admin_layer(self) -> AdminConfigApplyResult:
        return await refresh_admin_layer(self._agent_loop.config_orchestrator)

    async def _config_proxy_read(
        self, params: ConfigProxyReadParams
    ) -> ConfigProxyReadResponse:
        self._require_session(params.session_id)
        values = await asyncio.to_thread(get_current_proxy_settings)
        return ConfigProxyReadResponse(
            settings=ProxySettingsView(values=values, descriptions=SUPPORTED_PROXY_VARS)
        )

    async def _config_proxy_write(
        self, params: ConfigProxyWriteParams
    ) -> EmptyResponse:
        self._execution.require_idle()
        self._require_session(params.session_id)

        def write() -> None:
            for key, value in params.changes.items():
                if value:
                    set_proxy_var(key, value)
                else:
                    unset_proxy_var(key)

        try:
            await asyncio.to_thread(write)
        except ProxySetupError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        return EmptyResponse()

    async def _config_fields_read(
        self, params: ConfigFieldsReadParams
    ) -> ConfigFieldsReadResponse:
        self._require_session(params.session_id)
        orchestrator = self._agent_loop.config_orchestrator
        config = orchestrator.config
        layer_values = await collect_layer_values(orchestrator.layers)
        # Per-tool config editing is not exposed in the settings screen yet.
        fields = [
            wire
            for wire in build_field_wires(
                config, layer_values, popular=POPULAR_SETTINGS
            )
            if wire.name not in HIDDEN_SETTINGS
        ]
        return ConfigFieldsReadResponse(fields=fields, targets=self._config_targets())

    def _config_targets(self) -> list[str]:
        orchestrator = self._agent_loop.config_orchestrator
        names = {layer.name for layer in orchestrator.layers}
        writable = orchestrator.writable_layer_name
        targets = [writable]
        if OverridesLayer.NAME in names and OverridesLayer.NAME not in targets:
            targets.append(OverridesLayer.NAME)
        return targets

    def _agents_list(self, params: AgentsListParams) -> AgentsListResponse:
        self._require_session(params.session_id)
        active, agents = project_agents(self._agent_loop)
        return AgentsListResponse(active=active, agents=agents)

    async def _agent_install(
        self, params: AgentInstallParams, *, install: bool
    ) -> AgentsListResponse:
        installed = list(self._agent_loop.config.installed_agents)
        if install and params.agent_name not in installed:
            installed.append(params.agent_name)
        if not install:
            installed = [name for name in installed if name != params.agent_name]
        response = await self._config_write(
            ConfigWriteParams(
                session_id=params.session_id,
                ops=[
                    ConfigWriteOpWire(
                        op="set",
                        path="/installed_agents",
                        value=cast(JsonValue, installed),
                    )
                ],
                reason="app-server agents install",
            )
        )
        if response.rejected or response.failures:
            raise RequestFailure(
                ProtocolErrorCode.INTERNAL_ERROR,
                "; ".join(response.failures) or "Configuration edit rejected",
            )
        active, agents = project_agents(self._agent_loop)
        return AgentsListResponse(active=active, agents=agents)

    def _skills_list(self, params: SkillsListParams) -> SkillsListResponse:
        self._require_session(params.session_id)
        return SkillsListResponse(skills=project_skills(self._agent_loop))

    def _tools_list(self, params: ToolsListParams) -> ToolsListResponse:
        self._require_session(params.session_id)
        return ToolsListResponse(tools=project_tools(self._agent_loop))

    def _stats_read(self, params: StatsReadParams) -> StatsReadResponse:
        self._require_session(params.session_id)
        return StatsReadResponse(
            stats=project_stats(self._agent_loop), context_window=self._context_window()
        )

    def _context_window(self) -> int:
        try:
            return self._agent_loop.config.get_active_model().auto_compact_threshold
        except ValueError:
            return 0

    def _diagnostics_list(
        self, params: DiagnosticsListParams
    ) -> DiagnosticsListResponse:
        self._require_session(params.session_id)
        issues, hooks_count = project_diagnostics(self._agent_loop)
        return DiagnosticsListResponse(issues=issues, hooks_count=hooks_count)

    def _diagnostics_logs_read(
        self, params: DiagnosticsLogsReadParams
    ) -> DiagnosticsLogsReadResponse:
        self._require_session(params.session_id)
        logs = self._logs.get_logs(limit=params.limit, offset=params.offset)
        return DiagnosticsLogsReadResponse(logs=project_debug_logs(logs))

    def _connectors_read(self, params: ConnectorsReadParams) -> ConnectorsReadResponse:
        self._require_session(params.session_id)
        return ConnectorsReadResponse(counts=project_connectors(self._agent_loop))

    async def _connector_auth_read(
        self, params: ConnectorAuthReadParams
    ) -> ConnectorAuthReadResponse:
        self._require_session(params.session_id)
        registry = self._agent_loop.connector_registry
        if registry is None:
            return ConnectorAuthReadResponse()
        return ConnectorAuthReadResponse(url=await registry.get_auth_url(params.name))

    async def _connector_refresh(
        self, params: ConnectorRefreshParams
    ) -> ConnectorRefreshResponse:
        self._execution.require_idle()
        self._require_session(params.session_id)
        registry = self._agent_loop.connector_registry
        if registry is None:
            raise RequestFailure(
                ProtocolErrorCode.NOT_FOUND, "Connectors are not available"
            )
        tools = await registry.refresh_connector_async(params.name)
        await self._agent_loop.tool_manager.integrate_connectors_async()
        await self._agent_loop.refresh_system_prompt()
        return ConnectorRefreshResponse(
            tool_count=len(tools), runtime=self.runtime_snapshot()
        )

    def _config_mutation_response(self) -> ConfigMutationResponse:
        return ConfigMutationResponse(
            runtime=self.runtime_snapshot(),
            stripped_history_images=(
                self._agent_loop.count_history_images_unsupported_by_active_model()
            ),
        )

    def _require_session(self, session_id: str) -> None:
        if session_id != self._agent_loop.session_id:
            raise RequestFailure(
                ProtocolErrorCode.NOT_FOUND, f"Session not found: {session_id}"
            )


def _project_loop(loop: CoreScheduledLoop) -> ScheduledLoop:
    return ScheduledLoop(
        id=loop.id,
        prompt=loop.prompt,
        interval_seconds=loop.interval_seconds,
        next_fire_at=loop.next_fire_at,
    )
