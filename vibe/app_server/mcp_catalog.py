"""Backend-independent MCP catalog persistence and session convergence."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypeGuard, runtime_checkable

from vibe.app_server._dispatch import DispatchResult, RequestFailure, method_not_found
from vibe.app_server._mcp_auth import MCPAuthenticationService
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._session_backend_port import (
    ResolvedMCPCatalog,
    ResolvedMCPServerConfig,
    SessionBackend,
    SessionBackendError,
    SessionBackendRuntimeView,
    SessionMCPControl,
    SessionMCPSourceState,
    SessionMCPState,
)
from vibe.app_server.models import (
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
    MCPToolSummary,
)
from vibe.app_server.protocol import (
    MCPAddParams,
    MCPAddResponse,
    MCPAuthRequiredParams,
    MCPAuthUrlParams,
    MCPCatalogMutationResponse,
    MCPLoginParams,
    MCPLogoutParams,
    MCPReadParams,
    MCPReadResponse,
    MCPRefreshParams,
    MCPRemoveParams,
    MCPRemoveResponse,
    MCPToggleParams,
    ProtocolErrorCode,
    RuntimeSnapshot,
    RuntimeUpdatedParams,
)
from vibe.core.auth.mcp_oauth import MCPOAuthError
from vibe.core.config import MCPHttp, MCPOAuth, MCPServer, MCPStdio, MCPStreamableHttp
from vibe.core.config.mcp_servers import (
    MCPServerAddError,
    MCPServerRemoveError,
    PersistedMCPServerResult,
    RemovedMCPServerResult,
    persist_oauth_mcp_server,
    persist_remote_mcp_server,
    persist_stdio_mcp_server,
    remove_mcp_server,
)
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.types import ConcurrencyConflictError
from vibe.core.config.vibe_schema import VibeConfigSchema
from vibe.core.tools.mcp_settings import persist_mcp_toggle
from vibe.utils.mcp import format_tool_display_description

if TYPE_CHECKING:
    from vibe.app_server._plugin_mcp import (
        PluginMCPCatalog,
        PluginMCPServerEntry,
        PluginMCPSource,
        PluginMCPStatus,
    )

type Notify = Callable[[str, ProtocolModel], Awaitable[None]]
type SessionlessCatalogFactory = Callable[
    [], Awaitable[ConfigOrchestrator[VibeConfigSchema]]
]

_ALIASES = {
    "mcp/read": "mcp_catalog/read",
    "mcp/refresh": "mcp_catalog/refresh",
    "mcp/toggle": "mcp_catalog/toggle",
    "mcp/add": "mcp_catalog/add",
    "mcp/login": "mcp_catalog/login",
    "mcp/logout": "mcp_catalog/logout",
}

# Not ``None``, which already means something here: that the caller has no
# owner to name and wants the slot every sessionless one shares.
_OWNED_BY_ORCHESTRATOR = object()


@runtime_checkable
class SessionMCPCatalogBinding(Protocol):
    @property
    def mcp_config_orchestrator(self) -> ConfigOrchestrator[VibeConfigSchema]: ...


@runtime_checkable
class SessionPluginMCPBinding(Protocol):
    @property
    def plugin_mcp_catalog(self) -> PluginMCPCatalog: ...


@runtime_checkable
class SessionMCPProjectionSink(Protocol):
    def update_mcp_projection(self, state: MCPState) -> None: ...


class MCPCatalogService:
    """Own app configuration, OAuth coordination, and catalog-to-session calls."""

    def __init__(
        self,
        authentication: MCPAuthenticationService,
        *,
        sessionless_catalog_factory: SessionlessCatalogFactory | None = None,
    ) -> None:
        self._authentication = authentication
        self._sessionless_catalog_factory = sessionless_catalog_factory
        self._auth_required_seen: set[tuple[str, str, str, str | None]] = set()
        self._convergence_errors: dict[tuple[str, str], str] = {}

    @staticmethod
    def handles(method: str) -> bool:
        return method in _ALIASES or method.startswith("mcp_catalog/")

    @property
    def authentication(self) -> MCPAuthenticationService:
        return self._authentication

    async def resolve_catalog(
        self,
        orchestrator: ConfigOrchestrator[VibeConfigSchema],
        *,
        owner: object | None = _OWNED_BY_ORCHESTRATOR,
    ) -> ResolvedMCPCatalog:
        # The orchestrator stands for the session by default, which is what a
        # caller reading its own config wants. One whose orchestrator it does
        # not keep -- the session's is handed on as a copy -- says so instead
        # and names something it holds: the authentication service files the
        # binding under it weakly and would otherwise drop it mid-session.
        key = orchestrator if owner is _OWNED_BY_ORCHESTRATOR else owner
        return await self._resolved(orchestrator, owner=key)

    async def sessionless_orchestrator(self) -> ConfigOrchestrator[VibeConfigSchema]:
        if self._sessionless_catalog_factory is None:
            raise RequestFailure(
                ProtocolErrorCode.NOT_IMPLEMENTED,
                "Sessionless MCP catalog mutations are not configured",
            )
        return await self._sessionless_catalog_factory()

    async def accept_auth_required(
        self, params: MCPAuthRequiredParams, root: SessionBackend | None
    ) -> RuntimeUpdatedParams | None:
        if (
            root is None
            or root.session_id != params.session_id
            or not isinstance(root, SessionMCPCatalogBinding)
            or not isinstance(root, SessionMCPControl)
            or not isinstance(root, SessionBackendRuntimeView)
        ):
            return None
        state = await root.read_mcp()
        source = next(
            (candidate for candidate in state.sources if candidate.name == params.name),
            None,
        )
        if (
            source is None
            or source.status != "needs_auth"
            or source.descriptor_revision != params.descriptor_revision
        ):
            return None
        key = (
            params.session_id,
            params.name,
            params.descriptor_revision,
            params.observed_connection_revision,
        )
        if key in self._auth_required_seen:
            return None
        self._auth_required_seen.add(key)
        context = _CatalogContext.for_session(root)
        self._runtime(
            context, self._project(context, state), preserve_auth_required=key
        )
        return root.runtime_updated_params()

    async def prepare_config_reload(
        self, root: SessionBackend
    ) -> _CatalogReloadPlan | None:
        if not isinstance(root, SessionMCPCatalogBinding) or not isinstance(
            root, SessionMCPControl
        ):
            return None
        context = _CatalogContext.for_session(root)
        previous = await self._resolved(
            context.orchestrator, owner=context.catalog_owner
        )
        await context.orchestrator.reload()
        candidate = await self._resolved(
            context.orchestrator, owner=context.catalog_owner
        )
        restrictive = await self._suspend_restrictive_changes(
            context, previous, candidate
        )
        return _CatalogReloadPlan(previous=previous, restrictive_names=restrictive)

    async def finish_config_reload(
        self, root: SessionBackend, plan: _CatalogReloadPlan | None
    ) -> RuntimeSnapshot | None:
        if (
            plan is None
            or not isinstance(root, SessionMCPCatalogBinding)
            or not isinstance(root, SessionMCPControl)
        ):
            return None
        context = _CatalogContext.for_session(root)
        candidate = await self._resolved(
            context.orchestrator, owner=context.catalog_owner
        )
        restrictive = await self._suspend_restrictive_changes(
            context, plan.previous, candidate
        )
        affected = frozenset(server.name for server in candidate.servers) | frozenset(
            server.name for server in plan.previous.servers
        )
        try:
            state = await context.require_control().reconfigure_mcp(
                candidate, force_remote_discovery=False
            )
        except Exception:
            self._record_convergence_error(context, affected | restrictive)
            raise
        self._clear_convergence_errors(context, affected)
        return self._runtime(context, self._project(context, state))

    async def fail_config_reload(
        self, root: SessionBackend, plan: _CatalogReloadPlan | None
    ) -> None:
        if plan is None or not plan.restrictive_names:
            return
        if not isinstance(root, SessionMCPCatalogBinding) or not isinstance(
            root, SessionMCPControl
        ):
            return
        context = _CatalogContext.for_session(root)
        await self._restore_after_restrictive_failure(
            context, plan.previous, affected_names=plan.restrictive_names
        )

    async def dispatch(
        self,
        method: str,
        raw_params: dict[str, Any],
        *,
        root: SessionBackend | None,
        notify: Notify,
    ) -> DispatchResult:
        canonical = _ALIASES.get(method, method)
        match canonical:
            case "mcp_catalog/read":
                params = validate_wire(MCPReadParams, raw_params)
                context = await self._target(params.session_id, root)
                session_state = await context.require_control().read_mcp()
                response: ProtocolModel = MCPReadResponse(
                    mcp=self._project(context, session_state)
                )
                runtime_updated = False
            case "mcp_catalog/refresh":
                params = validate_wire(MCPRefreshParams, raw_params)
                context = await self._target(params.session_id, root)
                await context.orchestrator.reload()
                state = await self._converge(
                    context,
                    force_remote_discovery=True,
                    affected_names=frozenset(
                        server.name
                        for server in context.orchestrator.config.mcp_servers
                    ),
                )
                if state is None:
                    raise RuntimeError("A targeted MCP refresh did not converge")
                if context.plugin_mcp is not None:
                    await context.plugin_mcp.refresh_all()
                response = MCPCatalogMutationResponse(
                    runtime=self._runtime(context, self._project(context, state))
                )
                runtime_updated = True
            case "mcp_catalog/toggle":
                params = validate_wire(MCPToggleParams, raw_params)
                response, runtime_updated = await self._toggle(params, root)
            case "mcp_catalog/add":
                params = validate_wire(MCPAddParams, raw_params)
                response, runtime_updated = await self._add(params, root)
            case "mcp_catalog/remove":
                params = validate_wire(MCPRemoveParams, raw_params)
                response, runtime_updated = await self._remove(params, root)
            case "mcp_catalog/login":
                params = validate_wire(MCPLoginParams, raw_params)
                response, runtime_updated = await self._login(params, root, notify)
            case "mcp_catalog/logout":
                params = validate_wire(MCPLogoutParams, raw_params)
                response, runtime_updated = await self._logout(params, root)
            case _:
                raise method_not_found(method)
        return DispatchResult(response=response, runtime_updated=runtime_updated)

    async def _add(
        self, params: MCPAddParams, root: SessionBackend | None
    ) -> tuple[MCPAddResponse, bool]:
        context = await self._mutation_target(params.session_id, root)
        try:
            result = await persist_oauth_mcp_server(
                context.orchestrator,
                url=params.url,
                name=params.name,
                scopes=params.scopes,
                transport=params.transport,
            )
        except ConcurrencyConflictError as exc:
            raise RequestFailure(ProtocolErrorCode.CONFLICT, str(exc)) from exc
        except MCPServerAddError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        state = await self._converge(
            context,
            force_remote_discovery=False,
            affected_names=frozenset({result.server.name}),
        )
        runtime = (
            self._runtime(context, self._project(context, state))
            if state is not None
            else None
        )
        return (
            MCPAddResponse(
                name=result.server.name,
                url=result.server.url,
                created=result.created,
                runtime=runtime,
            ),
            state is not None,
        )

    async def _toggle(
        self, params: MCPToggleParams, root: SessionBackend | None
    ) -> tuple[MCPCatalogMutationResponse, bool]:
        if params.source == "connector":
            raise RequestFailure(
                ProtocolErrorCode.NOT_IMPLEMENTED,
                "Connector-owned MCP sources are not part of the MCP catalog",
            )
        context = await self._mutation_target(params.session_id, root)
        _reject_plugin_mutation(context, params.name)
        original = (
            await self._resolved(context.orchestrator, owner=context.catalog_owner)
            if context.control is not None and params.disabled
            else None
        )
        if context.control is not None and params.disabled:
            await context.control.suspend_mcp(
                name=params.name, tool_name=params.tool_name, reason="disable"
            )
        try:
            await persist_mcp_toggle(
                context.orchestrator,
                name=params.name,
                is_connector=False,
                disabled=params.disabled,
                tool_name=params.tool_name,
            )
        except ConcurrencyConflictError as exc:
            await self._restore_after_restrictive_failure(
                context, original, affected_names=frozenset({params.name})
            )
            raise RequestFailure(ProtocolErrorCode.CONFLICT, str(exc)) from exc
        except ValueError as exc:
            await self._restore_after_restrictive_failure(
                context, original, affected_names=frozenset({params.name})
            )
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        state = await self._converge(
            context,
            force_remote_discovery=False,
            affected_names=frozenset({params.name}),
        )
        runtime = (
            self._runtime(context, self._project(context, state))
            if state is not None
            else None
        )
        return MCPCatalogMutationResponse(runtime=runtime), state is not None

    async def _remove(
        self, params: MCPRemoveParams, root: SessionBackend | None
    ) -> tuple[MCPRemoveResponse, bool]:
        context = await self._mutation_target(params.session_id, root)
        _reject_plugin_mutation(context, params.name)
        configured = _server_named(context.orchestrator, params.name)
        original = (
            await self._resolved(context.orchestrator, owner=context.catalog_owner)
            if context.control is not None
            else None
        )
        if context.control is not None:
            await context.control.suspend_mcp(
                name=params.name, tool_name=None, reason="remove"
            )
        try:
            result = await _remove_server_with_credentials(
                self._authentication,
                context.orchestrator,
                configured=configured,
                name=params.name,
                owner=context.catalog_owner,
            )
        except ConcurrencyConflictError as exc:
            await self._restore_after_restrictive_failure(
                context, original, affected_names=frozenset({params.name})
            )
            raise RequestFailure(ProtocolErrorCode.CONFLICT, str(exc)) from exc
        except MCPServerRemoveError as exc:
            await self._restore_after_restrictive_failure(
                context, original, affected_names=frozenset({params.name})
            )
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        except (MCPOAuthError, ValueError) as exc:
            await self._restore_after_restrictive_failure(
                context, original, affected_names=frozenset({params.name})
            )
            raise RequestFailure(ProtocolErrorCode.INTERNAL_ERROR, str(exc)) from exc
        state = await self._converge(
            context,
            force_remote_discovery=False,
            affected_names=frozenset({params.name}),
        )
        runtime = (
            self._runtime(context, self._project(context, state))
            if state is not None
            else None
        )
        return (
            MCPRemoveResponse(
                name=result.name, removed=result.removed, runtime=runtime
            ),
            state is not None,
        )

    async def _login(
        self, params: MCPLoginParams, root: SessionBackend | None, notify: Notify
    ) -> tuple[MCPCatalogMutationResponse, bool]:
        context = await self._mutation_target(params.session_id, root)
        plugin_owned = context.plugin_entry(params.name) is not None
        # The catalog this session means by the name, handed to the service so
        # it does not decide ownership again over every session's bindings.
        owner = context.plugin_mcp if plugin_owned else context.catalog_owner
        if not plugin_owned:
            await self._authentication.bind_catalog(
                context.orchestrator.config.mcp_servers, owner=owner
            )

        async def publish_url(url: str) -> None:
            payload = MCPAuthUrlParams(name=params.name, url=url)
            await notify("mcp_catalog/authUrl", payload)
            await notify("mcp/authUrl", payload)

        try:
            descriptor_revision = await self._authentication.login(
                params.name, on_url=publish_url, owner=owner
            )
        except (MCPOAuthError, ValueError) as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        if plugin_owned:
            return await self._plugin_authorization_changed(context, params.name)
        state = None
        if context.control is not None:
            try:
                state = await context.control.authorization_changed(
                    name=params.name, descriptor_revision=descriptor_revision
                )
            except Exception:
                self._record_convergence_error(context, frozenset({params.name}))
                raise
            self._clear_convergence_errors(context, frozenset({params.name}))
        runtime = (
            self._runtime(context, self._project(context, state))
            if state is not None
            else None
        )
        return MCPCatalogMutationResponse(runtime=runtime), state is not None

    async def _logout(
        self, params: MCPLogoutParams, root: SessionBackend | None
    ) -> tuple[MCPCatalogMutationResponse, bool]:
        context = await self._mutation_target(params.session_id, root)
        plugin_owned = context.plugin_entry(params.name) is not None
        owner = context.plugin_mcp if plugin_owned else context.catalog_owner
        if not plugin_owned:
            await self._authentication.bind_catalog(
                context.orchestrator.config.mcp_servers, owner=owner
            )
        original = (
            await self._resolved(context.orchestrator, owner=context.catalog_owner)
            if context.control is not None and not plugin_owned
            else None
        )
        if context.control is not None and not plugin_owned:
            await context.control.suspend_mcp(
                name=params.name, tool_name=None, reason="logout"
            )
        try:
            descriptor_revision = await self._authentication.logout(
                params.name, owner=owner
            )
        except (MCPOAuthError, ValueError) as exc:
            await self._restore_after_restrictive_failure(
                context, original, affected_names=frozenset({params.name})
            )
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
        if plugin_owned:
            return await self._plugin_authorization_changed(context, params.name)
        state = None
        if context.control is not None:
            try:
                state = await context.control.authorization_changed(
                    name=params.name, descriptor_revision=descriptor_revision
                )
            except Exception:
                self._record_convergence_error(context, frozenset({params.name}))
                raise
            self._clear_convergence_errors(context, frozenset({params.name}))
        runtime = (
            self._runtime(context, self._project(context, state))
            if state is not None
            else None
        )
        return MCPCatalogMutationResponse(runtime=runtime), state is not None

    async def _plugin_authorization_changed(
        self, context: _CatalogContext, name: str
    ) -> tuple[MCPCatalogMutationResponse, bool]:
        # Convergence stops here rather than going through the MCP control
        # port: that port routes by config-owned name, and a plugin's source id
        # names nothing it could suspend. State is read only to reproject with.
        if context.plugin_mcp is None or context.control is None:
            return MCPCatalogMutationResponse(runtime=None), False
        await context.plugin_mcp.refresh(name)
        state = await context.control.read_mcp()
        return (
            MCPCatalogMutationResponse(
                runtime=self._runtime(context, self._project(context, state))
            ),
            True,
        )

    async def _converge(
        self,
        context: _CatalogContext,
        *,
        force_remote_discovery: bool,
        affected_names: frozenset[str],
    ) -> SessionMCPState | None:
        configuration = await self._resolved(
            context.orchestrator, owner=context.catalog_owner
        )
        if context.control is None:
            return None
        try:
            state = await context.control.reconfigure_mcp(
                configuration, force_remote_discovery=force_remote_discovery
            )
        except Exception:
            self._record_convergence_error(context, affected_names)
            raise
        self._clear_convergence_errors(context, affected_names)
        return state

    async def _restore_after_restrictive_failure(
        self,
        context: _CatalogContext,
        configuration: ResolvedMCPCatalog | None,
        *,
        affected_names: frozenset[str],
    ) -> None:
        if context.control is None or configuration is None:
            return
        try:
            await context.control.reconfigure_mcp(
                configuration, force_remote_discovery=False
            )
        except Exception:
            self._record_convergence_error(context, affected_names)
            return
        self._clear_convergence_errors(context, affected_names)

    async def _suspend_restrictive_changes(
        self,
        context: _CatalogContext,
        previous: ResolvedMCPCatalog,
        candidate: ResolvedMCPCatalog,
    ) -> frozenset[str]:
        if context.control is None:
            return frozenset()
        next_by_name = {server.name: server for server in candidate.servers}
        suspended: set[str] = set()
        for old in previous.servers:
            new = next_by_name.get(old.name)
            if (
                new is None
                or new.disabled
                or old.authorization.server_fingerprint
                != new.authorization.server_fingerprint
            ):
                await context.control.suspend_mcp(
                    name=old.name,
                    tool_name=None,
                    reason="replace" if new is not None else "remove",
                )
                suspended.add(old.name)
                continue
            for tool_name in new.disabled_tools - old.disabled_tools:
                await context.control.suspend_mcp(
                    name=old.name, tool_name=tool_name, reason="disable"
                )
                suspended.add(old.name)
        return frozenset(suspended)

    def _record_convergence_error(
        self, context: _CatalogContext, affected_names: frozenset[str]
    ) -> None:
        if context.root is None:
            return
        for name in affected_names:
            self._convergence_errors[(context.root.session_id, name)] = (
                "MCP source configuration did not converge in this session"
            )

    def _clear_convergence_errors(
        self, context: _CatalogContext, affected_names: frozenset[str]
    ) -> None:
        if context.root is None:
            return
        for name in affected_names:
            self._convergence_errors.pop((context.root.session_id, name), None)

    async def _resolved(
        self,
        orchestrator: ConfigOrchestrator[VibeConfigSchema],
        *,
        owner: object | None,
    ) -> ResolvedMCPCatalog:
        servers = orchestrator.config.mcp_servers
        await self._authentication.bind_catalog(servers, owner=owner)
        resolved = tuple(self._resolve_server(server) for server in servers)
        payload = [
            {
                "name": server.name,
                "transport": server.transport,
                "url": server.url,
                "command": server.command,
                "args": server.args,
                "cwd": str(server.cwd) if server.cwd is not None else None,
                "env": dict(server.env),
                "authorization": {
                    "server_name": server.authorization.server_name,
                    "server_fingerprint": server.authorization.server_fingerprint,
                    "kind": server.authorization.kind,
                },
                "prompt": server.prompt,
                "startup_timeout_s": server.startup_timeout_s,
                "tool_timeout_s": server.tool_timeout_s,
                "sampling_enabled": server.sampling_enabled,
                "disabled": server.disabled,
                "disabled_tools": sorted(server.disabled_tools),
            }
            for server in resolved
        ]
        revision = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return ResolvedMCPCatalog(revision=revision, servers=resolved)

    def _resolve_server(self, server: MCPServer) -> ResolvedMCPServerConfig:
        reference = self._authentication.reference_for(server)
        if isinstance(server, MCPStdio):
            argv = server.argv()
            return ResolvedMCPServerConfig(
                name=server.name,
                transport="stdio",
                url=None,
                command=argv[0] if argv else None,
                args=tuple(argv[1:]),
                cwd=Path(server.cwd).expanduser().resolve() if server.cwd else None,
                env=server.env,
                authorization=reference,
                prompt=server.prompt,
                startup_timeout_s=server.startup_timeout_sec,
                tool_timeout_s=server.tool_timeout_sec,
                sampling_enabled=server.sampling_enabled,
                disabled=server.disabled,
                disabled_tools=frozenset(server.disabled_tools),
            )
        return ResolvedMCPServerConfig(
            name=server.name,
            transport=server.transport,
            url=server.url,
            command=None,
            args=(),
            cwd=None,
            env={},
            authorization=reference,
            prompt=server.prompt,
            startup_timeout_s=server.startup_timeout_sec,
            tool_timeout_s=server.tool_timeout_sec,
            sampling_enabled=server.sampling_enabled,
            disabled=server.disabled,
            disabled_tools=frozenset(server.disabled_tools),
        )

    async def _target(
        self, session_id: str, root: SessionBackend | None
    ) -> _CatalogContext:
        if root is None or root.session_id != session_id:
            raise RequestFailure(
                ProtocolErrorCode.NOT_FOUND, f"Session not found: {session_id}"
            )
        return _CatalogContext.for_session(root)

    async def _mutation_target(
        self, session_id: str | None, root: SessionBackend | None
    ) -> _CatalogContext:
        if session_id is not None:
            return await self._target(session_id, root)
        if root is not None:
            raise RequestFailure(
                ProtocolErrorCode.CONFLICT,
                "A sessionless MCP catalog mutation cannot target an active session implicitly",
            )
        orchestrator = await self.sessionless_orchestrator()
        return _CatalogContext(orchestrator, None, None)

    def _project(self, context: _CatalogContext, state: SessionMCPState) -> MCPState:
        orchestrator = context.orchestrator
        session_id = context.root.session_id if context.root is not None else None
        current_mcp = (
            context.root.runtime_updated_params().runtime.mcp
            if isinstance(context.root, SessionBackendRuntimeView)
            else MCPState()
        )
        connector_sources = [
            source
            for source in current_mcp.sources
            if source.kind is MCPSourceKind.CONNECTOR
        ]
        convergence_errors = {
            name: error
            for (failed_session_id, name), error in self._convergence_errors.items()
            if failed_session_id == session_id
        }
        sources, errors = project_mcp_sources(
            orchestrator,
            state,
            convergence_errors=convergence_errors,
            plugin_sources=(
                () if context.plugin_mcp is None else context.plugin_mcp.sources()
            ),
        )
        return MCPState(
            sources=[*sources, *connector_sources],
            discovery_errors=errors,
            connector_error=current_mcp.connector_error,
        )

    def _runtime(
        self,
        context: _CatalogContext,
        state: MCPState,
        *,
        preserve_auth_required: tuple[str, str, str, str | None] | None = None,
    ) -> RuntimeSnapshot:
        root = context.root
        if root is None or not isinstance(root, SessionBackendRuntimeView):
            raise SessionBackendError(
                ProtocolErrorCode.NOT_IMPLEMENTED,
                "The selected session backend cannot project runtime state",
            )
        if isinstance(root, SessionMCPProjectionSink):
            root.update_mcp_projection(state)
        self._clear_resolved_auth_required(
            root.session_id, preserve=preserve_auth_required
        )
        return root.runtime_updated_params().runtime

    def _clear_resolved_auth_required(
        self,
        session_id: str,
        *,
        preserve: tuple[str, str, str, str | None] | None = None,
    ) -> None:
        self._auth_required_seen = {
            key
            for key in self._auth_required_seen
            if key[0] != session_id or key == preserve
        }


class SessionlessMCPCatalog:
    """Narrow command facade that never constructs a Harness session."""

    def __init__(self, service: MCPCatalogService, notify: Notify) -> None:
        self._service = service
        self._notify = notify

    async def dispatch(self, method: str, params: ProtocolModel) -> ProtocolModel:
        result = await self._service.dispatch(
            method,
            params.model_dump(mode="json", by_alias=True),
            root=None,
            notify=self._notify,
        )
        return result.response

    async def add_server(
        self,
        server: MCPHttp | MCPStreamableHttp | MCPStdio,
        *,
        login: bool,
        on_oauth_url: Callable[[str], Awaitable[None]],
        on_persisted: Callable[[PersistedMCPServerResult[Any]], None] | None = None,
    ) -> PersistedMCPServerResult[Any]:
        orchestrator = await self._service.sessionless_orchestrator()
        result = (
            await persist_stdio_mcp_server(orchestrator, server)
            if isinstance(server, MCPStdio)
            else await persist_remote_mcp_server(orchestrator, server)
        )
        await self._service.authentication.bind_catalog(orchestrator.config.mcp_servers)
        if on_persisted is not None:
            on_persisted(result)
        if login and _is_oauth(result.server):
            await self._service.authentication.login(
                result.server.name, on_url=on_oauth_url
            )
        return result

    async def remove_server(self, name: str) -> RemovedMCPServerResult:
        orchestrator = await self._service.sessionless_orchestrator()
        server = _server_named(orchestrator, name)
        await self._service.authentication.bind_catalog(orchestrator.config.mcp_servers)
        try:
            return await _remove_server_with_credentials(
                self._service.authentication,
                orchestrator,
                configured=server,
                name=name,
                owner=None,
            )
        except (MCPOAuthError, ValueError) as exc:
            raise MCPServerRemoveError(
                f"Failed to remove OAuth credentials for `{name}`: {exc}"
            ) from exc


def create_sessionless_mcp_catalog(
    factory: SessionlessCatalogFactory, *, notify: Notify | None = None
) -> SessionlessMCPCatalog:
    async def ignore_notification(_method: str, _params: ProtocolModel) -> None:
        return None

    service = MCPCatalogService(
        MCPAuthenticationService(), sessionless_catalog_factory=factory
    )
    return SessionlessMCPCatalog(service, notify or ignore_notification)


class _CatalogContext:
    def __init__(
        self,
        orchestrator: ConfigOrchestrator[VibeConfigSchema],
        control: SessionMCPControl | None,
        root: SessionBackend | None,
        plugin_mcp: PluginMCPCatalog | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.control = control
        self.root = root
        self.plugin_mcp = plugin_mcp

    @classmethod
    def for_session(cls, root: SessionBackend) -> _CatalogContext:
        if not isinstance(root, SessionMCPCatalogBinding) or not isinstance(
            root, SessionMCPControl
        ):
            raise RequestFailure(
                ProtocolErrorCode.NOT_IMPLEMENTED,
                "The selected session backend does not support MCP catalog control",
            )
        return cls(
            root.mcp_config_orchestrator,
            root,
            root,
            root.plugin_mcp_catalog
            if isinstance(root, SessionPluginMCPBinding)
            else None,
        )

    def require_control(self) -> SessionMCPControl:
        if self.control is None:
            raise RuntimeError("A targeted MCP catalog operation has no control port")
        return self.control

    @property
    def catalog_owner(self) -> object | None:
        # What the authentication service files this session's configured
        # servers under. None for a sessionless mutation: its orchestrator is
        # built fresh per call, so keying on it would file the binding under
        # something nothing holds, and it has no session to be confused with.
        return self.orchestrator if self.root is not None else None

    def plugin_entry(self, name: str) -> PluginMCPServerEntry | None:
        # A configured server owns the name outright, which is the answer
        # projection and ``_bound_server`` already give. Plugin resolution drops
        # a shadowed source id, but only the next time it runs, and no command
        # in a running session runs it; reading the entry as plugin-owned until
        # then would route a login away from the row /mcp shows and reject a
        # toggle of it.
        if (
            self.plugin_mcp is None
            or _server_named(self.orchestrator, name) is not None
        ):
            return None
        return self.plugin_mcp.entry(name)


@dataclass(frozen=True, slots=True)
class _CatalogReloadPlan:
    previous: ResolvedMCPCatalog
    restrictive_names: frozenset[str]


def _project_source(
    server: MCPServer, state: SessionMCPSourceState | None, *, convergence_failed: bool
) -> MCPSourceSummary:
    if convergence_failed:
        status = MCPSourceStatus.UNAVAILABLE
        tools = [] if state is None else _project_tools(state)
    elif state is None:
        status = (
            MCPSourceStatus.DISABLED if server.disabled else MCPSourceStatus.UNAVAILABLE
        )
        tools: list[MCPToolSummary] = []
    else:
        status = MCPSourceStatus(state.status)
        tools = _project_tools(state)
    return MCPSourceSummary(
        name=server.name,
        kind=MCPSourceKind.SERVER,
        transport=server.transport,
        status=status,
        tools=tools,
    )


def project_mcp_sources(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    state: SessionMCPState,
    *,
    convergence_errors: Mapping[str, str] | None = None,
    plugin_sources: Sequence[PluginMCPSource] = (),
) -> tuple[list[MCPSourceSummary], dict[str, str]]:
    convergence_errors = convergence_errors or {}
    states = {source.name: source for source in state.sources}
    sources = [
        _project_source(
            server,
            states.get(server.name),
            convergence_failed=server.name in convergence_errors,
        )
        for server in orchestrator.config.mcp_servers
    ]
    errors = dict(state.discovery_errors)
    for server in orchestrator.config.mcp_servers:
        if not server.disabled and server.name not in states:
            errors.setdefault(
                server.name, "MCP source configuration is not active in this session"
            )
        if error := convergence_errors.get(server.name):
            errors[server.name] = error
    configured = {server.name for server in orchestrator.config.mcp_servers}
    # Plugin resolution drops a source id a configured server already holds;
    # this covers the window before a config change is resolved against, where
    # projecting both would put two rows under one name.
    for plugin_source in plugin_sources:
        if plugin_source.name in configured:
            continue
        sources.append(_project_plugin_source(plugin_source))
        if plugin_source.error is not None:
            errors.setdefault(plugin_source.name, plugin_source.error)
    return sources, errors


_PLUGIN_SOURCE_STATUS: dict[PluginMCPStatus, MCPSourceStatus] = {
    "connected": MCPSourceStatus.CONNECTED,
    "needs_auth": MCPSourceStatus.NEEDS_AUTH,
    "unavailable": MCPSourceStatus.UNAVAILABLE,
}


def _project_plugin_source(source: PluginMCPSource) -> MCPSourceSummary:
    # Deliberately the same kind as a configured server: only ``plugin_name``
    # says where it came from. Nothing here is toggleable.
    return MCPSourceSummary(
        name=source.name,
        kind=MCPSourceKind.SERVER,
        transport=source.transport,
        status=_PLUGIN_SOURCE_STATUS[source.status],
        tools=[
            MCPToolSummary(
                name=tool.name,
                # No source name to strip: the ``[alias] `` prefix comes from
                # the legacy backend's tool classes, never from a descriptor.
                description=format_tool_display_description(tool.description),
            )
            for tool in source.tools
        ],
        plugin_name=source.plugin_name,
    )


def _reject_plugin_mutation(context: _CatalogContext, name: str) -> None:
    # No ``[[mcp_servers]]`` entry backs a plugin's server, so a toggle or a
    # remove would not edit it -- it would write a config-owned entry shadowing
    # the plugin's under the same name.
    entry = context.plugin_entry(name)
    if entry is None:
        return
    raise RequestFailure(
        ProtocolErrorCode.INVALID_PARAMS,
        f"MCP server '{name}' is managed by the '{entry.plugin_name}' plugin and "
        "cannot be toggled or removed from the MCP catalog.",
    )


def _project_tools(state: SessionMCPSourceState) -> list[MCPToolSummary]:
    return [
        MCPToolSummary(
            name=tool.remote_name,
            description=format_tool_display_description(
                tool.description, source_name=state.name
            ),
            enabled=tool.enabled,
        )
        for tool in state.tools
    ]


def _server_named(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], name: str
) -> MCPServer | None:
    return next(
        (server for server in orchestrator.config.mcp_servers if server.name == name),
        None,
    )


def _is_oauth(server: object) -> TypeGuard[MCPHttp | MCPStreamableHttp]:
    return isinstance(server, MCPHttp | MCPStreamableHttp) and isinstance(
        server.auth, MCPOAuth
    )


async def _remove_server_with_credentials(
    authentication: MCPAuthenticationService,
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    *,
    configured: MCPServer | None,
    name: str,
    owner: object | None,
) -> RemovedMCPServerResult:
    if not _is_oauth(configured):
        return await remove_mcp_server(orchestrator, name)
    async with authentication.credential_removal(configured.name, owner=owner):
        return await remove_mcp_server(orchestrator, name)


__all__ = [
    "MCPCatalogService",
    "SessionMCPCatalogBinding",
    "SessionMCPProjectionSink",
    "SessionPluginMCPBinding",
    "SessionlessMCPCatalog",
    "create_sessionless_mcp_catalog",
    "project_mcp_sources",
]
