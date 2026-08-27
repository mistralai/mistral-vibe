"""Backend-independent MCP catalog persistence and session convergence."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Protocol, TypeGuard, runtime_checkable

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


@runtime_checkable
class SessionMCPCatalogBinding(Protocol):
    @property
    def mcp_config_orchestrator(self) -> ConfigOrchestrator[VibeConfigSchema]: ...


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
        self, orchestrator: ConfigOrchestrator[VibeConfigSchema]
    ) -> ResolvedMCPCatalog:
        return await self._resolved(orchestrator)

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
        context = _CatalogContext(root.mcp_config_orchestrator, root, root)
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
        context = _CatalogContext(root.mcp_config_orchestrator, root, root)
        previous = await self._resolved(context.orchestrator)
        await context.orchestrator.reload()
        candidate = await self._resolved(context.orchestrator)
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
        context = _CatalogContext(root.mcp_config_orchestrator, root, root)
        candidate = await self._resolved(context.orchestrator)
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
        context = _CatalogContext(root.mcp_config_orchestrator, root, root)
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
        original = (
            await self._resolved(context.orchestrator)
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
        configured = _server_named(context.orchestrator, params.name)
        original = (
            await self._resolved(context.orchestrator)
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
        await self._authentication.bind_catalog(context.orchestrator.config.mcp_servers)

        async def publish_url(url: str) -> None:
            payload = MCPAuthUrlParams(name=params.name, url=url)
            await notify("mcp_catalog/authUrl", payload)
            await notify("mcp/authUrl", payload)

        try:
            descriptor_revision = await self._authentication.login(
                params.name, on_url=publish_url
            )
        except (MCPOAuthError, ValueError) as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
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
        await self._authentication.bind_catalog(context.orchestrator.config.mcp_servers)
        original = (
            await self._resolved(context.orchestrator)
            if context.control is not None
            else None
        )
        if context.control is not None:
            await context.control.suspend_mcp(
                name=params.name, tool_name=None, reason="logout"
            )
        try:
            descriptor_revision = await self._authentication.logout(params.name)
        except (MCPOAuthError, ValueError) as exc:
            await self._restore_after_restrictive_failure(
                context, original, affected_names=frozenset({params.name})
            )
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc
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

    async def _converge(
        self,
        context: _CatalogContext,
        *,
        force_remote_discovery: bool,
        affected_names: frozenset[str],
    ) -> SessionMCPState | None:
        configuration = await self._resolved(context.orchestrator)
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
        self, orchestrator: ConfigOrchestrator[VibeConfigSchema]
    ) -> ResolvedMCPCatalog:
        servers = orchestrator.config.mcp_servers
        await self._authentication.bind_catalog(servers)
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
        if not isinstance(root, SessionMCPCatalogBinding) or not isinstance(
            root, SessionMCPControl
        ):
            raise RequestFailure(
                ProtocolErrorCode.NOT_IMPLEMENTED,
                "The selected session backend does not support MCP catalog control",
            )
        return _CatalogContext(root.mcp_config_orchestrator, root, root)

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
        states = {source.name: source for source in state.sources}
        sources = [
            _project_source(
                server,
                states.get(server.name),
                convergence_failed=(session_id, server.name)
                in self._convergence_errors,
            )
            for server in orchestrator.config.mcp_servers
        ]
        errors = dict(state.discovery_errors)
        for server in orchestrator.config.mcp_servers:
            if not server.disabled and server.name not in states:
                errors.setdefault(
                    server.name,
                    "MCP source configuration is not active in this session",
                )
            if session_id is not None and (
                error := self._convergence_errors.get((session_id, server.name))
            ):
                errors[server.name] = error
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
                self._service.authentication, orchestrator, configured=server, name=name
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
    ) -> None:
        self.orchestrator = orchestrator
        self.control = control
        self.root = root

    def require_control(self) -> SessionMCPControl:
        if self.control is None:
            raise RuntimeError("A targeted MCP catalog operation has no control port")
        return self.control


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


def _project_tools(state: SessionMCPSourceState) -> list[MCPToolSummary]:
    return [
        MCPToolSummary(
            name=tool.remote_name, description=tool.description, enabled=tool.enabled
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
) -> RemovedMCPServerResult:
    if not _is_oauth(configured):
        return await remove_mcp_server(orchestrator, name)
    async with authentication.credential_removal(configured.name):
        return await remove_mcp_server(orchestrator, name)


__all__ = [
    "MCPCatalogService",
    "SessionMCPCatalogBinding",
    "SessionMCPProjectionSink",
    "SessionlessMCPCatalog",
    "create_sessionless_mcp_catalog",
]
