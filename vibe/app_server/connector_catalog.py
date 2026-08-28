"""Host-owned connector discovery, cache, and resolved selection."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, BinaryIO, Literal, Protocol, cast, runtime_checkable
from uuid import uuid4

import httpx
from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from vibe.app_server._connector_observability import (
    add_connector_cache_read,
    record_connector_catalog_operation,
)
from vibe.app_server._dispatch import DispatchResult, RequestFailure, method_not_found
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._session_backend_port import (
    ConnectorAuthAction,
    ConnectorAuthRequest,
    ResolvedConnector,
    ResolvedConnectorCatalog,
    ResolvedConnectorSelection,
    ResolvedConnectorSetting,
    ResolvedConnectorTool,
    SessionBackend,
    SessionBackendError,
    SessionBackendRuntimeView,
    SessionConnectorControl,
    SessionConnectorState,
)
from vibe.app_server.models import ConnectorCounts
from vibe.app_server.protocol import (
    ConnectorAuthFailedParams,
    ConnectorAuthReadParams,
    ConnectorAuthReadResponse,
    ConnectorAuthRequiredParams,
    ConnectorAuthUrlParams,
    ConnectorCatalogAuthRequestParams,
    ConnectorCatalogAuthRequestResponse,
    ConnectorCatalogEntryView,
    ConnectorCatalogMutationResponse,
    ConnectorCatalogReadParams,
    ConnectorCatalogReadResponse,
    ConnectorCatalogRefreshParams,
    ConnectorCatalogToggleParams,
    ConnectorCatalogToolView,
    ConnectorCatalogView,
    ConnectorRefreshParams,
    ConnectorRefreshResponse,
    ConnectorSelectionView,
    ConnectorsReadParams,
    ConnectorsReadResponse,
    ProtocolErrorCode,
    RuntimeUpdatedParams,
    SessionConnectorSourceView,
    SessionConnectorStateView,
    SessionConnectorToolView,
)
from vibe.core.config import VibeConfigSchema, resolve_api_key
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.types import ConcurrencyConflictError
from vibe.core.paths import CONNECTOR_BOOTSTRAP_CACHE_FILE
from vibe.core.tools.mcp_settings import persist_mcp_toggle
from vibe.core.utils.matching import name_matches
from vibe.observability.logging import logger
from vibe.utils.http import (
    VibeAsyncHTTPClient,
    build_ssl_context,
    get_server_url_from_api_base,
)

type BootstrapFetcher = Callable[[str, str], Awaitable[object]]
type CacheDisposition = Literal["memory", "fresh_cache", "not_loaded"]
type Notify = Callable[[str, ProtocolModel], Awaitable[None]]
type SessionlessCatalogFactory = Callable[
    [], Awaitable[ConfigOrchestrator[VibeConfigSchema]]
]

_DEFAULT_BASE_URL = "https://api.mistral.ai"
_BOOTSTRAP_CACHE_FORMAT = 2
_BOOTSTRAP_CACHE_TTL_SECONDS = 10 * 60
_BOOTSTRAP_TIMEOUT_SECONDS = 30.0
_MAX_CONNECTORS = 256
_MAX_TOOLS_PER_CONNECTOR = 128
_MAX_INPUT_SCHEMA_BYTES = 64 * 1024
_MAX_DIAGNOSTICS_PER_CONNECTOR = 3
_MAX_DIAGNOSTIC_CHARACTERS = 512
_MAX_CACHE_ENTRY_BYTES = 2 * 1024 * 1024
_MAX_PUBLIC_NAME_CHARACTERS = 256
_ALIASES = {
    "connectors/read": "connector_catalog/read",
    "connectors/refresh": "connector_catalog/refresh",
}
_tracer = trace.get_tracer(__name__)


class ConnectorCatalogError(RuntimeError):
    """Base error for host-owned connector catalog operations."""


class ConnectorCatalogUnavailableError(ConnectorCatalogError):
    """The account-wide connector bootstrap could not be loaded."""


class ConnectorCatalogValidationError(ConnectorCatalogError):
    """The connector bootstrap payload violates the bounded catalog contract."""


@runtime_checkable
class SessionConnectorCatalogBinding(Protocol):
    @property
    def connector_config_orchestrator(self) -> ConfigOrchestrator[VibeConfigSchema]: ...


class _BootstrapStatus(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    is_ready: bool = False


class _BootstrapAuthAction(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    type: str = "none"


class _BootstrapTool(BaseModel):
    model_config = ConfigDict(
        extra="ignore", frozen=True, validate_by_alias=True, validate_by_name=True
    )

    name: str
    description: str | None = None
    input_schema: dict[str, JsonValue] = Field(
        default_factory=dict, alias="inputSchema"
    )


class _BootstrapConnector(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: str | None = None
    name: str | None = None
    status: _BootstrapStatus = Field(default_factory=_BootstrapStatus)
    tools: list[_BootstrapTool] = Field(default_factory=list)
    auth_action: _BootstrapAuthAction | None = None
    bootstrap_errors: JsonValue = None


class _BootstrapPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    connectors: list[_BootstrapConnector] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ConnectorCatalogReadResult:
    catalog: ResolvedConnectorCatalog | None
    disposition: CacheDisposition


@dataclass(frozen=True, slots=True)
class _ConnectorProvider:
    fingerprint: str
    base_url: str
    api_key: str


@dataclass(frozen=True, slots=True)
class _MemoryCatalog:
    catalog: ResolvedConnectorCatalog
    stored_at: int


@dataclass(frozen=True, slots=True)
class _CacheHit:
    catalog: ResolvedConnectorCatalog
    stored_at: int


@dataclass(frozen=True, slots=True)
class _CatalogContext:
    orchestrator: ConfigOrchestrator[VibeConfigSchema]
    control: SessionConnectorControl | None
    root: SessionBackend | None

    def require_control(self) -> SessionConnectorControl:
        if self.control is None:
            raise RuntimeError("A targeted connector operation has no control port")
        return self.control


@dataclass(frozen=True, slots=True)
class _ConnectorCandidate:
    catalog: ResolvedConnectorCatalog
    selection: ResolvedConnectorSelection
    force: bool

    @property
    def key(self) -> tuple[str, str]:
        return self.catalog.revision, self.selection.selection_revision


@dataclass(frozen=True, slots=True)
class _TerminalConvergence:
    key: tuple[str, str]
    code: ProtocolErrorCode
    message: str


@dataclass(frozen=True, slots=True)
class ConnectorRuntimeAuthorization:
    runtime_updated: RuntimeUpdatedParams
    start_broker: Callable[[], None]
    release_reservation: Callable[[], None]


def normalize_connector_alias(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    result = normalized.strip("_-")[:_MAX_PUBLIC_NAME_CHARACTERS]
    return result or "unnamed"


def connector_cache_fingerprint(api_key: str, base_url: str | None) -> str:
    normalized_base_url = (base_url or _DEFAULT_BASE_URL).rstrip("/")
    return hashlib.sha256(f"{normalized_base_url}\0{api_key}".encode()).hexdigest()


def resolve_connector_selection(
    config: VibeConfigSchema,
    catalog: ResolvedConnectorCatalog | None,
    *,
    implicit_source_enabled: bool,
) -> ResolvedConnectorSelection:
    selection = ResolvedConnectorSelection(
        selection_revision="",
        enable_connectors=config.enable_connectors,
        implicit_source_enabled=implicit_source_enabled,
        connector_settings=tuple(
            ResolvedConnectorSetting(
                alias=connector.name,
                disabled=connector.disabled,
                disabled_tools=frozenset(connector.disabled_tools),
            )
            for connector in config.connectors
        ),
        enabled_tools=tuple(config.enabled_tools),
        disabled_tools=tuple(config.disabled_tools),
    )
    return replace(
        selection,
        selection_revision=_selection_revision(
            selection,
            (
                (connector.alias, tuple(tool.raw_name for tool in connector.tools))
                for connector in (catalog.connectors if catalog is not None else ())
            ),
        ),
    )


def _selection_revision(
    selection: ResolvedConnectorSelection, sources: Iterable[tuple[str, Iterable[str]]]
) -> str:
    source_items = {alias: tuple(tool_names) for alias, tool_names in sources}
    decisions = [
        {
            "alias": alias,
            "sourceEnabled": connector_source_enabled(selection, alias),
            "tools": [
                {
                    "name": tool_name,
                    "enabled": connector_tool_enabled(
                        selection, alias=alias, raw_tool_name=tool_name
                    ),
                }
                for tool_name in sorted(tool_names)
            ],
        }
        for alias, tool_names in sorted(source_items.items())
    ]
    return hashlib.sha256(_canonical_json(decisions)).hexdigest()


def _resolve_selection_for_state(
    config: VibeConfigSchema,
    state: SessionConnectorState,
    *,
    implicit_source_enabled: bool,
) -> ResolvedConnectorSelection:
    selection = resolve_connector_selection(
        config, None, implicit_source_enabled=implicit_source_enabled
    )
    return replace(
        selection,
        selection_revision=_selection_revision(
            selection,
            tuple(
                (source.alias, tuple(tool.raw_name for tool in source.tools))
                for source in state.sources
            ),
        ),
    )


def connector_source_enabled(selection: ResolvedConnectorSelection, alias: str) -> bool:
    if not selection.enable_connectors:
        return False
    setting = next(
        (item for item in selection.connector_settings if item.alias == alias), None
    )
    if setting is None:
        return selection.implicit_source_enabled
    return not setting.disabled


def connector_tool_enabled(
    selection: ResolvedConnectorSelection, *, alias: str, raw_tool_name: str
) -> bool:
    if not connector_source_enabled(selection, alias):
        return False
    setting = next(
        (item for item in selection.connector_settings if item.alias == alias), None
    )
    if setting is not None and raw_tool_name in setting.disabled_tools:
        return False
    published_name = f"connector_{alias}_{raw_tool_name}"
    if selection.enabled_tools and not name_matches(
        published_name, list(selection.enabled_tools)
    ):
        return False
    return not (
        selection.disabled_tools
        and name_matches(published_name, list(selection.disabled_tools))
    )


@contextmanager
def _cache_update_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.with_name(f".{path.name}.lock").open("a+b")
    try:
        _acquire_cache_file_lock(lock_file)
    except BaseException:
        lock_file.close()
        raise
    try:
        yield
    finally:
        _release_cache_file_lock(lock_file)
        lock_file.close()


def _acquire_cache_file_lock(file: BinaryIO) -> None:
    if os.name == "nt":
        msvcrt = cast(Any, __import__("msvcrt"))
        file.seek(0)
        if file.read(1) == b"":
            file.write(b"\0")
            file.flush()
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(file.fileno(), fcntl.LOCK_EX)


def _release_cache_file_lock(file: BinaryIO) -> None:
    if os.name == "nt":
        msvcrt = cast(Any, __import__("msvcrt"))
        file.seek(0)
        msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(file.fileno(), fcntl.LOCK_UN)


class ConnectorCatalogCache:
    """Read and atomically write bounded connector bootstrap cache records."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def read(self, provider_fingerprint: str, *, now: int) -> _CacheHit | None:
        entries = self._read_entries()
        raw_entry = entries.get(provider_fingerprint)
        return _parse_cache_entry(provider_fingerprint, raw_entry, now=now)

    def write(self, catalog: ResolvedConnectorCatalog, *, stored_at: int) -> None:
        raw_entry = _cache_entry(catalog, stored_at=stored_at)
        if _json_size(raw_entry) > _MAX_CACHE_ENTRY_BYTES:
            raise ConnectorCatalogValidationError(
                "Connector bootstrap cache entry exceeds 2 MiB"
            )

        with _cache_update_lock(self._path):
            entries = self._read_entries()
            fresh_entries = {
                fingerprint: entry
                for fingerprint, entry in entries.items()
                if _parse_cache_entry(fingerprint, entry, now=stored_at) is not None
            }
            fresh_entries[catalog.provider_fingerprint] = raw_entry
            self._write_entries(fresh_entries)

    def _read_entries(self) -> dict[str, object]:
        try:
            with self._path.open(encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): value for key, value in payload.items()}

    def _write_entries(self, entries: Mapping[str, object]) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self._path.name}.", suffix=".tmp", dir=self._path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                descriptor = -1
                json.dump(entries, file, separators=(",", ":"), sort_keys=True)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self._path)
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
            raise


class ConnectorCatalogService:
    """Own account connector discovery, cache lifetime, and selection defaults."""

    def __init__(
        self,
        *,
        implicit_source_enabled: bool,
        cache_path: Path | None = None,
        fetch_bootstrap: BootstrapFetcher | None = None,
        clock: Callable[[], int] | None = None,
        sessionless_catalog_factory: SessionlessCatalogFactory | None = None,
    ) -> None:
        self._implicit_source_enabled = implicit_source_enabled
        self._backend: Literal["legacy", "unified"] = (
            "unified" if implicit_source_enabled else "legacy"
        )
        self._cache = ConnectorCatalogCache(
            cache_path or CONNECTOR_BOOTSTRAP_CACHE_FILE.path
        )
        self._fetch_bootstrap = fetch_bootstrap or _fetch_bootstrap
        self._clock = clock or (lambda: int(time.time()))
        self._memory: dict[str, _MemoryCatalog] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._sessionless_catalog_factory = sessionless_catalog_factory
        self._auth_requests_seen: set[tuple[str, str, str, str]] = set()
        self._broker_tasks: set[asyncio.Task[None]] = set()
        self._pending_convergence: dict[str, _ConnectorCandidate] = {}
        self._terminal_convergence: dict[str, _TerminalConvergence] = {}

    @staticmethod
    def handles(method: str) -> bool:
        return method.startswith("connector_catalog/") or method in {
            "connectors/read",
            "connectors/refresh",
            "connectors/auth/read",
        }

    async def dispatch(
        self,
        method: str,
        raw_params: dict[str, object],
        *,
        root: SessionBackend | None,
        notify: Notify,
    ) -> DispatchResult:
        if method == "connectors/read":
            params = validate_wire(ConnectorsReadParams, raw_params)
            response: ProtocolModel = await self._compat_read(params, root)
            return DispatchResult(response=response)
        if method == "connectors/refresh":
            params = validate_wire(ConnectorRefreshParams, raw_params)
            response = await self._compat_refresh(params, root)
            return DispatchResult(response=response, runtime_updated=True)
        if method == "connectors/auth/read":
            params = validate_wire(ConnectorAuthReadParams, raw_params)
            response = await self._compat_auth_read(params, root)
            return DispatchResult(response=response)
        canonical = _ALIASES.get(method, method)
        match canonical:
            case "connector_catalog/read":
                params = validate_wire(ConnectorCatalogReadParams, raw_params)
                response: ProtocolModel = await self._read(params, root)
                runtime_updated = False
                after_response = None
            case "connector_catalog/refresh":
                params = validate_wire(ConnectorCatalogRefreshParams, raw_params)
                response, runtime_updated = await self._refresh_request(params, root)
                after_response = None
            case "connector_catalog/toggle":
                params = validate_wire(ConnectorCatalogToggleParams, raw_params)
                response, runtime_updated = await self._toggle(params, root)
                after_response = None
            case "connector_catalog/auth/request":
                params = validate_wire(ConnectorCatalogAuthRequestParams, raw_params)
                response, broker = await self._request_auth(params, root, notify)
                runtime_updated = False
                after_response = broker
            case _:
                raise method_not_found(method)
        return DispatchResult(
            response=response,
            runtime_updated=runtime_updated,
            after_response=after_response,
        )

    async def _compat_read(
        self, params: ConnectorsReadParams, root: SessionBackend | None
    ) -> ConnectorsReadResponse:
        context = await self._target(params.session_id, root)
        state = await context.require_control().read_connectors()
        return ConnectorsReadResponse(
            counts=ConnectorCounts(
                connected=sum(source.status == "connected" for source in state.sources),
                total=len(state.sources),
            )
        )

    async def _compat_refresh(
        self, params: ConnectorRefreshParams, root: SessionBackend | None
    ) -> ConnectorRefreshResponse:
        mutation, _runtime_updated = await self._refresh_request(
            ConnectorCatalogRefreshParams(session_id=params.session_id), root
        )
        context = await self._target(params.session_id, root)
        state = await context.require_control().read_connectors()
        source = next(
            (item for item in state.sources if item.alias == params.name), None
        )
        if source is None:
            raise RequestFailure(
                ProtocolErrorCode.NOT_FOUND, f"Connector not found: {params.name}"
            )
        if mutation.runtime is None:
            raise RequestFailure(
                ProtocolErrorCode.INTERNAL_ERROR,
                "Connector refresh did not produce a runtime projection",
            )
        return ConnectorRefreshResponse(
            tool_count=sum(tool.enabled for tool in source.tools),
            runtime=mutation.runtime,
        )

    async def _compat_auth_read(
        self, params: ConnectorAuthReadParams, root: SessionBackend | None
    ) -> ConnectorAuthReadResponse:
        context = await self._target(params.session_id, root)
        request = await context.require_control().request_connector_auth(
            alias=params.name
        )
        provider = _resolve_provider(context.orchestrator.config)
        if provider is None:
            return ConnectorAuthReadResponse()
        return ConnectorAuthReadResponse(
            url=await _connector_auth_url(provider, request.raw_connector_id)
        )

    async def _read(
        self, params: ConnectorCatalogReadParams, root: SessionBackend | None
    ) -> ConnectorCatalogReadResponse:
        context = await self._read_context(params.session_id, root)
        result = self.read_catalog(context.orchestrator)
        session = (
            await context.require_control().read_connectors()
            if context.control is not None
            else None
        )
        return ConnectorCatalogReadResponse(
            catalog=_project_catalog(result),
            selections=_project_selections(context.orchestrator.config, result.catalog),
            session=_project_session(session) if session is not None else None,
        )

    async def _refresh_request(
        self, params: ConnectorCatalogRefreshParams, root: SessionBackend | None
    ) -> tuple[ConnectorCatalogMutationResponse, bool]:
        context = await self._read_context(params.session_id, root)
        try:
            catalog = await self.resolve_catalog(
                context.orchestrator, force_refresh=True
            )
        except ConnectorCatalogError as exc:
            raise RequestFailure(ProtocolErrorCode.INTERNAL_ERROR, str(exc)) from exc
        if catalog is None:
            return ConnectorCatalogMutationResponse(), False
        selection = self.resolve_selection(context.orchestrator, catalog)
        if context.control is None:
            return ConnectorCatalogMutationResponse(
                catalog_revision=catalog.revision,
                selection_revision=selection.selection_revision,
            ), False
        state = await self._accept_candidate(
            context,
            _ConnectorCandidate(catalog=catalog, selection=selection, force=True),
        )
        return self._mutation_response(context, catalog, selection, state), True

    async def _toggle(
        self, params: ConnectorCatalogToggleParams, root: SessionBackend | None
    ) -> tuple[ConnectorCatalogMutationResponse, bool]:
        _validate_toggle(params.alias, params.tool_name)
        context = await self._mutation_context(params.session_id, root)
        accepted = (
            await context.require_control().read_connectors()
            if context.control is not None
            else None
        )
        if accepted is not None and not any(
            source.alias == params.alias for source in accepted.sources
        ):
            raise RequestFailure(
                ProtocolErrorCode.NOT_FOUND,
                f"Connector alias not found in the accepted session catalog: {params.alias}",
            )

        catalog_result = self.read_catalog(context.orchestrator)
        catalog = catalog_result.catalog
        if accepted is not None and (
            catalog is None or catalog.revision != accepted.accepted_catalog_revision
        ):
            raise RequestFailure(
                ProtocolErrorCode.CONFLICT,
                "The host connector catalog does not match the target session",
            )

        candidate_selection: ResolvedConnectorSelection | None = None

        async def preflight(candidate_config: VibeConfigSchema) -> None:
            nonlocal candidate_selection
            candidate_selection = resolve_connector_selection(
                candidate_config,
                catalog,
                implicit_source_enabled=self._implicit_source_enabled,
            )
            live_control = self._live_control(context)
            if context.control is not None or live_control is None:
                return
            live_state = await live_control.read_connectors()
            live_candidate = _resolve_selection_for_state(
                candidate_config,
                live_state,
                implicit_source_enabled=self._implicit_source_enabled,
            )
            if (
                live_candidate.selection_revision
                == live_state.accepted_selection_revision
            ):
                return
            raise RequestFailure(
                ProtocolErrorCode.CONFLICT,
                "The connector selection affects a live session; provide sessionId",
            )

        try:
            await persist_mcp_toggle(
                context.orchestrator,
                name=params.alias,
                is_connector=True,
                disabled=params.disabled,
                tool_name=params.tool_name,
                preflight=preflight,
            )
        except ConcurrencyConflictError as exc:
            raise RequestFailure(ProtocolErrorCode.CONFLICT, str(exc)) from exc
        except ValueError as exc:
            raise RequestFailure(ProtocolErrorCode.INVALID_PARAMS, str(exc)) from exc

        if candidate_selection is None:
            raise RuntimeError("Connector config mutation skipped candidate preflight")

        pending = catalog is None or not any(
            connector.alias == params.alias for connector in catalog.connectors
        )
        if context.control is None or catalog is None:
            return (
                ConnectorCatalogMutationResponse(
                    catalog_revision=catalog.revision if catalog is not None else None,
                    selection_revision=candidate_selection.selection_revision,
                    pending_selection=pending,
                ),
                False,
            )
        state = await self._accept_candidate(
            context,
            _ConnectorCandidate(
                catalog=catalog, selection=candidate_selection, force=False
            ),
        )
        return self._mutation_response(
            context, catalog, candidate_selection, state
        ), True

    async def _request_auth(
        self,
        params: ConnectorCatalogAuthRequestParams,
        root: SessionBackend | None,
        notify: Notify,
    ) -> tuple[ConnectorCatalogAuthRequestResponse, Callable[[], None]]:
        _validate_toggle(params.alias, None)
        context = await self._target(params.session_id, root)
        request = await context.require_control().request_connector_auth(
            alias=params.alias
        )
        if request.alias != params.alias or request.session_id != params.session_id:
            raise RequestFailure(
                ProtocolErrorCode.CONFLICT,
                "The connector authorization request does not match the target",
            )
        request_id, start_broker, _ = self._reserve_authorization(
            request, context=context, notify=notify, emit_required=True
        )

        return (
            ConnectorCatalogAuthRequestResponse(
                request_id=request_id,
                session_id=request.session_id,
                alias=request.alias,
                accepted_catalog_revision=request.accepted_catalog_revision,
            ),
            start_broker,
        )

    async def accept_auth_required(
        self,
        params: ConnectorAuthRequiredParams,
        *,
        raw_connector_id: str,
        action: str,
        root: SessionBackend | None,
        notify: Notify,
    ) -> ConnectorRuntimeAuthorization | None:
        context = await self._target(params.session_id, root)
        request = await context.require_control().request_connector_auth(
            alias=params.alias
        )
        if (
            request.raw_connector_id != raw_connector_id
            or request.accepted_catalog_revision != params.accepted_catalog_revision
            or request.action != action
        ):
            return None
        request = replace(request, reason="gateway_rejected")
        try:
            _request_id, start_broker, release_reservation = (
                self._reserve_authorization(
                    request, context=context, notify=notify, emit_required=False
                )
            )
        except RequestFailure as exc:
            if exc.code is ProtocolErrorCode.CONFLICT:
                return None
            raise
        if context.root is None or not isinstance(
            context.root, SessionBackendRuntimeView
        ):
            release_reservation()
            return None
        try:
            return ConnectorRuntimeAuthorization(
                runtime_updated=context.root.runtime_updated_params(),
                start_broker=start_broker,
                release_reservation=release_reservation,
            )
        except BaseException:
            release_reservation()
            raise

    def _reserve_authorization(
        self,
        request: ConnectorAuthRequest,
        *,
        context: _CatalogContext,
        notify: Notify,
        emit_required: bool,
    ) -> tuple[str, Callable[[], None], Callable[[], None]]:
        key = (
            request.session_id,
            request.raw_connector_id,
            request.accepted_catalog_revision,
            request.action,
        )
        if key in self._auth_requests_seen:
            raise RequestFailure(
                ProtocolErrorCode.CONFLICT,
                "Connector authorization is already pending for this catalog revision",
            )
        self._auth_requests_seen.add(key)
        request_id = str(uuid4())
        started = False
        reserved = True

        def release_reservation() -> None:
            nonlocal reserved
            if started or not reserved:
                return
            self._auth_requests_seen.discard(key)
            reserved = False

        def start_broker() -> None:
            nonlocal reserved, started
            if started or not reserved:
                return
            started = True
            reserved = False
            task = asyncio.create_task(
                self._broker_authorization(
                    request_id=request_id,
                    request=request,
                    context=context,
                    notify=notify,
                    emit_required=emit_required,
                    reservation_key=key,
                ),
                name=f"connector-auth-{request_id}",
            )
            self._broker_tasks.add(task)
            task.add_done_callback(self._broker_tasks.discard)

        return request_id, start_broker, release_reservation

    async def _broker_authorization(
        self,
        *,
        request_id: str,
        request: ConnectorAuthRequest,
        context: _CatalogContext,
        notify: Notify,
        emit_required: bool,
        reservation_key: tuple[str, str, str, str],
    ) -> None:
        started_at = time.perf_counter()
        outcome = "failure"
        required = ConnectorAuthRequiredParams(
            session_id=request.session_id,
            alias=request.alias,
            accepted_catalog_revision=request.accepted_catalog_revision,
            reason=request.reason,
        )
        try:
            with _tracer.start_as_current_span(
                "connector_catalog.authorization"
            ) as span:
                span.set_attribute("mistral_ai.vibe.harness.backend", self._backend)
                if emit_required:
                    await notify("connector_catalog/authRequired", required)
                state = await context.require_control().read_connectors()
                if (
                    state.accepted_catalog_revision != request.accepted_catalog_revision
                    or not any(
                        source.raw_id == request.raw_connector_id
                        and source.alias == request.alias
                        for source in state.sources
                    )
                ):
                    outcome = "stale"
                    span.set_attribute("mistral_ai.vibe.connector.outcome", outcome)
                    await notify(
                        "connector_catalog/authFailed",
                        ConnectorAuthFailedParams(
                            **required.model_dump(),
                            request_id=request_id,
                            code="stale_request",
                        ),
                    )
                    return
                provider = _resolve_provider(context.orchestrator.config)
                if provider is None:
                    outcome = "unavailable"
                    span.set_attribute("mistral_ai.vibe.connector.outcome", outcome)
                    await notify(
                        "connector_catalog/authFailed",
                        ConnectorAuthFailedParams(
                            **required.model_dump(),
                            request_id=request_id,
                            code="auth_url_unavailable",
                        ),
                    )
                    return
                url = await _connector_auth_url(provider, request.raw_connector_id)
                if url is None:
                    outcome = "unavailable"
                    span.set_attribute("mistral_ai.vibe.connector.outcome", outcome)
                    await notify(
                        "connector_catalog/authFailed",
                        ConnectorAuthFailedParams(
                            **required.model_dump(),
                            request_id=request_id,
                            code="auth_url_unavailable",
                        ),
                    )
                    return
                await notify(
                    "connector_catalog/authUrl",
                    ConnectorAuthUrlParams(
                        **required.model_dump(), request_id=request_id, url=url
                    ),
                )
                outcome = "success"
                span.set_attribute("mistral_ai.vibe.connector.outcome", outcome)
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        finally:
            self._auth_requests_seen.discard(reservation_key)
            record_connector_catalog_operation(
                time.perf_counter() - started_at,
                operation="authorization",
                outcome=outcome,
                backend=self._backend,
            )
            logger.debug(
                "Connector authorization broker completed",
                extra={
                    "connector_operation": "authorization",
                    "connector_outcome": outcome,
                },
            )

    async def _read_context(
        self, session_id: str | None, root: SessionBackend | None
    ) -> _CatalogContext:
        if session_id is not None:
            return await self._target(session_id, root)
        if root is not None and isinstance(root, SessionConnectorCatalogBinding):
            return _CatalogContext(root.connector_config_orchestrator, None, root)
        return _CatalogContext(await self._sessionless_orchestrator(), None, None)

    async def _mutation_context(
        self, session_id: str | None, root: SessionBackend | None
    ) -> _CatalogContext:
        if session_id is not None:
            return await self._target(session_id, root)
        if (
            root is not None
            and isinstance(root, SessionConnectorCatalogBinding)
            and isinstance(root, SessionConnectorControl)
        ):
            return _CatalogContext(root.connector_config_orchestrator, None, root)
        if root is not None:
            raise RequestFailure(
                ProtocolErrorCode.NOT_IMPLEMENTED,
                "The selected session backend does not support connector catalog control",
            )
        return _CatalogContext(await self._sessionless_orchestrator(), None, None)

    async def _target(
        self, session_id: str, root: SessionBackend | None
    ) -> _CatalogContext:
        if root is None or root.session_id != session_id:
            raise RequestFailure(
                ProtocolErrorCode.NOT_FOUND, f"Session not found: {session_id}"
            )
        if not isinstance(root, SessionConnectorCatalogBinding) or not isinstance(
            root, SessionConnectorControl
        ):
            raise RequestFailure(
                ProtocolErrorCode.NOT_IMPLEMENTED,
                "The selected session backend does not support connector catalog control",
            )
        return _CatalogContext(root.connector_config_orchestrator, root, root)

    async def _sessionless_orchestrator(self) -> ConfigOrchestrator[VibeConfigSchema]:
        if self._sessionless_catalog_factory is None:
            raise RequestFailure(
                ProtocolErrorCode.NOT_IMPLEMENTED,
                "Sessionless connector catalog operations are not configured",
            )
        return await self._sessionless_catalog_factory()

    def _mutation_response(
        self,
        context: _CatalogContext,
        catalog: ResolvedConnectorCatalog,
        selection: ResolvedConnectorSelection,
        state: SessionConnectorState,
    ) -> ConnectorCatalogMutationResponse:
        runtime = None
        if context.root is not None and isinstance(
            context.root, SessionBackendRuntimeView
        ):
            runtime = context.root.runtime_updated_params().runtime
        return ConnectorCatalogMutationResponse(
            catalog_revision=catalog.revision,
            selection_revision=selection.selection_revision,
            accepted_catalog_revision=state.accepted_catalog_revision,
            accepted_selection_revision=state.accepted_selection_revision,
            route_revision=state.route_revision,
            runtime=runtime,
        )

    @staticmethod
    def _live_control(context: _CatalogContext) -> SessionConnectorControl | None:
        if context.control is not None:
            return context.control
        if isinstance(context.root, SessionConnectorControl):
            return context.root
        return None

    async def _accept_candidate(
        self, context: _CatalogContext, candidate: _ConnectorCandidate
    ) -> SessionConnectorState:
        started_at = time.perf_counter()
        control = context.require_control()
        if context.root is None:
            raise RuntimeError("A connector convergence target has no session backend")
        session_id = context.root.session_id
        terminal = self._terminal_convergence.get(session_id)
        if terminal is not None and terminal.key == candidate.key:
            record_connector_catalog_operation(
                time.perf_counter() - started_at,
                operation="convergence",
                outcome="rejected",
                backend=self._backend,
            )
            raise RequestFailure(terminal.code, terminal.message)
        if terminal is not None:
            self._terminal_convergence.pop(session_id, None)

        try:
            state = await control.reconfigure_connectors(
                candidate.catalog, candidate.selection, force=candidate.force
            )
        except SessionBackendError as exc:
            if exc.code is ProtocolErrorCode.CONFLICT:
                self._pending_convergence[session_id] = candidate
                outcome = "busy"
            else:
                self._pending_convergence.pop(session_id, None)
                self._terminal_convergence[session_id] = _TerminalConvergence(
                    key=candidate.key, code=exc.code, message=str(exc)
                )
                outcome = "rejected"
            record_connector_catalog_operation(
                time.perf_counter() - started_at,
                operation="convergence",
                outcome=outcome,
                backend=self._backend,
            )
            logger.debug(
                "Connector catalog convergence deferred or rejected",
                extra={
                    "connector_operation": "convergence",
                    "connector_outcome": outcome,
                },
            )
            raise
        except Exception:
            self._pending_convergence.pop(session_id, None)
            record_connector_catalog_operation(
                time.perf_counter() - started_at,
                operation="convergence",
                outcome="failure",
                backend=self._backend,
            )
            logger.warning(
                "Connector catalog convergence failed",
                extra={
                    "connector_operation": "convergence",
                    "connector_outcome": "failure",
                },
            )
            raise

        self._pending_convergence.pop(session_id, None)
        self._terminal_convergence.pop(session_id, None)
        record_connector_catalog_operation(
            time.perf_counter() - started_at,
            operation="convergence",
            outcome="success",
            backend=self._backend,
        )
        logger.debug(
            "Connector catalog convergence completed",
            extra={
                "connector_operation": "convergence",
                "connector_outcome": "success",
            },
        )
        return state

    async def converge_pending_connector_candidate(
        self, session_id: str, root: SessionBackend | None
    ) -> RuntimeUpdatedParams | None:
        candidate = self._pending_convergence.get(session_id)
        if candidate is None:
            return None
        try:
            context = await self._target(session_id, root)
            await self._accept_candidate(context, candidate)
        except (RequestFailure, SessionBackendError) as exc:
            if getattr(exc, "code", None) is not ProtocolErrorCode.CONFLICT:
                logger.warning(
                    "Connector candidate was rejected at the idle boundary",
                    extra={
                        "connector_operation": "convergence",
                        "connector_outcome": "rejected",
                    },
                )
            return None
        except Exception:
            logger.warning(
                "Connector candidate failed at the idle boundary",
                extra={
                    "connector_operation": "convergence",
                    "connector_outcome": "failure",
                },
            )
            return None
        if isinstance(root, SessionBackendRuntimeView):
            return root.runtime_updated_params()
        return None

    def discard_session(self, session_id: str) -> None:
        self._pending_convergence.pop(session_id, None)
        self._terminal_convergence.pop(session_id, None)
        self._auth_requests_seen = {
            key for key in self._auth_requests_seen if key[0] != session_id
        }

    def resolve_selection(
        self,
        orchestrator: ConfigOrchestrator[VibeConfigSchema],
        catalog: ResolvedConnectorCatalog | None,
    ) -> ResolvedConnectorSelection:
        return resolve_connector_selection(
            orchestrator.config,
            catalog,
            implicit_source_enabled=self._implicit_source_enabled,
        )

    def read_catalog(
        self, orchestrator: ConfigOrchestrator[VibeConfigSchema]
    ) -> ConnectorCatalogReadResult:
        provider = _resolve_provider(orchestrator.config)
        if provider is None:
            return _catalog_read_result(None, "not_loaded", backend=self._backend)
        now = self._clock()
        memory = self._memory.get(provider.fingerprint)
        if memory is not None and _is_fresh(memory.stored_at, now=now):
            return _catalog_read_result(memory.catalog, "memory", backend=self._backend)
        hit = self._cache.read(provider.fingerprint, now=now)
        if hit is None:
            return _catalog_read_result(None, "not_loaded", backend=self._backend)
        self._memory[provider.fingerprint] = _MemoryCatalog(
            catalog=hit.catalog, stored_at=hit.stored_at
        )
        return _catalog_read_result(hit.catalog, "fresh_cache", backend=self._backend)

    async def resolve_catalog(
        self,
        orchestrator: ConfigOrchestrator[VibeConfigSchema],
        *,
        force_refresh: bool = False,
    ) -> ResolvedConnectorCatalog | None:
        provider = _resolve_provider(orchestrator.config)
        if provider is None:
            return None
        lock = self._locks.setdefault(provider.fingerprint, asyncio.Lock())
        async with lock:
            if not force_refresh:
                cached = self.read_catalog(orchestrator)
                if cached.catalog is not None:
                    return cached.catalog
            return await self._refresh(provider)

    async def _refresh(self, provider: _ConnectorProvider) -> ResolvedConnectorCatalog:
        started_at = time.perf_counter()
        with _tracer.start_as_current_span("connector_catalog.bootstrap") as span:
            span.set_attribute("mistral_ai.vibe.harness.backend", self._backend)
            try:
                payload = await self._fetch_bootstrap(
                    provider.base_url, provider.api_key
                )
                catalog = _resolve_catalog(payload, provider.fingerprint)
            except asyncio.CancelledError:
                span.set_attribute("mistral_ai.vibe.connector.outcome", "cancelled")
                record_connector_catalog_operation(
                    time.perf_counter() - started_at,
                    operation="bootstrap",
                    outcome="cancelled",
                    backend=self._backend,
                )
                raise
            except ConnectorCatalogValidationError:
                span.set_attribute("mistral_ai.vibe.connector.outcome", "invalid")
                record_connector_catalog_operation(
                    time.perf_counter() - started_at,
                    operation="bootstrap",
                    outcome="invalid",
                    backend=self._backend,
                )
                logger.warning(
                    "Connector catalog bootstrap was invalid",
                    extra={
                        "connector_operation": "bootstrap",
                        "connector_outcome": "invalid",
                    },
                )
                raise
            except Exception as exc:
                span.set_attribute("mistral_ai.vibe.connector.outcome", "unavailable")
                record_connector_catalog_operation(
                    time.perf_counter() - started_at,
                    operation="bootstrap",
                    outcome="unavailable",
                    backend=self._backend,
                )
                logger.warning(
                    "Failed to refresh the connector catalog",
                    extra={
                        "connector_operation": "bootstrap",
                        "connector_outcome": "unavailable",
                        "error_type": type(exc).__name__,
                    },
                )
                raise ConnectorCatalogUnavailableError(
                    _redacted_bootstrap_error(exc)
                ) from exc
            span.set_attribute("mistral_ai.vibe.connector.outcome", "success")
            record_connector_catalog_operation(
                time.perf_counter() - started_at,
                operation="bootstrap",
                outcome="success",
                backend=self._backend,
            )

        stored_at = self._clock()
        cache_started_at = time.perf_counter()
        try:
            await asyncio.to_thread(self._cache.write, catalog, stored_at=stored_at)
        except OSError as exc:
            record_connector_catalog_operation(
                time.perf_counter() - cache_started_at,
                operation="cache_write",
                outcome="failure",
                backend=self._backend,
            )
            logger.warning(
                "Failed to write the connector catalog cache",
                extra={
                    "connector_operation": "cache_write",
                    "connector_outcome": "failure",
                    "error_type": type(exc).__name__,
                },
            )
        else:
            record_connector_catalog_operation(
                time.perf_counter() - cache_started_at,
                operation="cache_write",
                outcome="success",
                backend=self._backend,
            )
        self._memory[provider.fingerprint] = _MemoryCatalog(
            catalog=catalog, stored_at=stored_at
        )
        return catalog


async def _fetch_bootstrap(base_url: str, api_key: str) -> object:
    url = f"{base_url.rstrip('/')}/v1/connectors/bootstrap"
    async with VibeAsyncHTTPClient(
        timeout=_BOOTSTRAP_TIMEOUT_SECONDS, verify=build_ssl_context()
    ) as client:
        response = await client.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            params={"include_auth_actionable_connectors": "true"},
        )
        response.raise_for_status()
        return response.json()


async def _connector_auth_url(
    provider: _ConnectorProvider, raw_connector_id: str
) -> str | None:
    http_client = VibeAsyncHTTPClient(verify=build_ssl_context(), follow_redirects=True)
    try:
        from mistralai.client import Mistral

        sdk_client = Mistral(
            api_key=provider.api_key,
            server_url=provider.base_url,
            async_client=http_client,
        )
        async with sdk_client as client:
            result = await client.beta.connectors.get_auth_url_async(
                connector_id_or_name=raw_connector_id
            )
        return result.auth_url
    except Exception:
        logger.warning(
            "Failed to obtain a connector authorization URL",
            extra={"provider_fingerprint": provider.fingerprint[:12]},
        )
        return None
    finally:
        await http_client.aclose()


def _catalog_read_result(
    catalog: ResolvedConnectorCatalog | None,
    disposition: CacheDisposition,
    *,
    backend: Literal["legacy", "unified"],
) -> ConnectorCatalogReadResult:
    add_connector_cache_read(disposition=disposition, backend=backend)
    logger.debug(
        "Connector catalog cache read",
        extra={
            "connector_operation": "cache_read",
            "connector_cache_disposition": disposition,
            "harness_backend": backend,
        },
    )
    return ConnectorCatalogReadResult(catalog=catalog, disposition=disposition)


def _project_catalog(result: ConnectorCatalogReadResult) -> ConnectorCatalogView:
    catalog = result.catalog
    return ConnectorCatalogView(
        disposition=result.disposition,
        catalog_revision=catalog.revision if catalog is not None else None,
        connectors=(
            [_project_catalog_entry(connector) for connector in catalog.connectors]
            if catalog is not None
            else []
        ),
    )


def _project_catalog_entry(connector: ResolvedConnector) -> ConnectorCatalogEntryView:
    return ConnectorCatalogEntryView(
        alias=connector.alias,
        display_name=connector.display_name,
        readiness=_connector_readiness(connector),
        auth_action=connector.auth_action,
        tools=[
            ConnectorCatalogToolView(name=tool.raw_name, description=tool.description)
            for tool in connector.tools
        ],
        diagnostic="; ".join(connector.diagnostics) or None,
    )


def _connector_readiness(
    connector: ResolvedConnector,
) -> Literal["ready", "needs_auth", "needs_setup", "unavailable"]:
    if connector.ready:
        return "ready"
    if connector.auth_action == "oauth":
        return "needs_auth"
    if connector.auth_action == "credentials_setup":
        return "needs_setup"
    return "unavailable"


def _project_selections(
    config: VibeConfigSchema, catalog: ResolvedConnectorCatalog | None
) -> list[ConnectorSelectionView]:
    resolved_aliases = (
        {connector.alias for connector in catalog.connectors}
        if catalog is not None
        else set()
    )
    return [
        ConnectorSelectionView(
            alias=selection.name,
            disabled=selection.disabled,
            disabled_tools=selection.disabled_tools,
            state="resolved" if selection.name in resolved_aliases else "pending",
        )
        for selection in config.connectors
    ]


def _project_session(state: SessionConnectorState) -> SessionConnectorStateView:
    return SessionConnectorStateView(
        accepted_catalog_revision=state.accepted_catalog_revision,
        accepted_selection_revision=state.accepted_selection_revision,
        route_revision=state.route_revision,
        sources=[
            SessionConnectorSourceView(
                alias=source.alias,
                display_name=source.display_name,
                status=source.status,
                tools=[
                    SessionConnectorToolView(
                        name=tool.raw_name,
                        description=tool.description,
                        enabled=tool.enabled,
                    )
                    for tool in source.tools
                ],
                error=source.error,
            )
            for source in state.sources
        ],
    )


def _validate_toggle(alias: str, tool_name: str | None) -> None:
    if alias != normalize_connector_alias(alias):
        raise RequestFailure(
            ProtocolErrorCode.INVALID_PARAMS,
            "Connector alias must already be normalized",
        )
    if tool_name is not None and (
        not tool_name.strip() or len(tool_name) > _MAX_PUBLIC_NAME_CHARACTERS
    ):
        raise RequestFailure(
            ProtocolErrorCode.INVALID_PARAMS,
            "Connector tool name must contain 1 to 256 characters",
        )


def _resolve_provider(config: VibeConfigSchema) -> _ConnectorProvider | None:
    if not config.enable_connectors:
        return None
    provider = config.get_mistral_provider()
    if provider is None:
        return None
    api_key_env = provider.api_key_env_var or "MISTRAL_API_KEY"
    api_key = resolve_api_key(api_key_env)
    if not api_key:
        return None
    base_url = get_server_url_from_api_base(provider.api_base) or _DEFAULT_BASE_URL
    base_url = base_url.rstrip("/")
    return _ConnectorProvider(
        fingerprint=connector_cache_fingerprint(api_key, base_url),
        base_url=base_url,
        api_key=api_key,
    )


def _resolve_catalog(
    payload: object, provider_fingerprint: str
) -> ResolvedConnectorCatalog:
    try:
        parsed = _BootstrapPayload.model_validate(payload)
    except ValidationError as exc:
        raise ConnectorCatalogValidationError(
            "Connector bootstrap payload is malformed"
        ) from exc
    if len(parsed.connectors) > _MAX_CONNECTORS:
        raise ConnectorCatalogValidationError(
            f"Connector bootstrap exceeds {_MAX_CONNECTORS} connectors"
        )

    raw_ids: set[str] = set()
    prepared_connectors: list[tuple[str, str, _BootstrapConnector]] = []
    for raw_connector in parsed.connectors:
        raw_id = (raw_connector.id or "").strip()
        if not raw_id:
            continue
        if raw_id in raw_ids:
            raise ConnectorCatalogValidationError(
                "Connector bootstrap contains a duplicate connector ID"
            )
        raw_ids.add(raw_id)
        if len(raw_connector.tools) > _MAX_TOOLS_PER_CONNECTOR:
            raise ConnectorCatalogValidationError(
                f"Connector {raw_id!r} exceeds {_MAX_TOOLS_PER_CONNECTOR} tools"
            )
        display_name = (raw_connector.name or raw_id).strip() or raw_id
        prepared_connectors.append((raw_id, display_name, raw_connector))

    aliases: set[str] = set()
    connectors: list[ResolvedConnector] = []
    for raw_id, display_name, raw_connector in sorted(
        prepared_connectors, key=lambda item: item[0]
    ):
        alias = _unique_alias(normalize_connector_alias(display_name), aliases)
        tools = tuple(
            _resolve_tool(tool, raw_id=raw_id) for tool in raw_connector.tools
        )
        tool_names = [tool.raw_name for tool in tools]
        if len(tool_names) != len(set(tool_names)):
            raise ConnectorCatalogValidationError(
                f"Connector {raw_id!r} contains duplicate tool names"
            )
        tools = tuple(sorted(tools, key=lambda tool: tool.raw_name))
        connectors.append(
            ResolvedConnector(
                raw_id=raw_id,
                alias=alias,
                display_name=display_name,
                ready=raw_connector.status.is_ready,
                auth_action=_auth_action(raw_connector.auth_action),
                tools=tools,
                diagnostics=_bounded_diagnostics(raw_connector.bootstrap_errors),
            )
        )

    revision_payload = [_connector_revision_payload(item) for item in connectors]
    revision = hashlib.sha256(_canonical_json(revision_payload)).hexdigest()
    catalog = ResolvedConnectorCatalog(
        provider_fingerprint=provider_fingerprint,
        revision=revision,
        connectors=tuple(connectors),
    )
    if _json_size(_cache_entry(catalog, stored_at=0)) > _MAX_CACHE_ENTRY_BYTES:
        raise ConnectorCatalogValidationError(
            "Connector bootstrap cache projection exceeds 2 MiB"
        )
    return catalog


def _resolve_tool(tool: _BootstrapTool, *, raw_id: str) -> ResolvedConnectorTool:
    name = tool.name.strip()
    if not name:
        raise ConnectorCatalogValidationError(
            f"Connector {raw_id!r} contains a tool without a name"
        )
    if _json_size(tool.input_schema) > _MAX_INPUT_SCHEMA_BYTES:
        raise ConnectorCatalogValidationError(
            f"Connector tool {name!r} input schema exceeds 64 KiB"
        )
    return ResolvedConnectorTool(
        raw_name=name,
        description=tool.description,
        input_schema=dict(tool.input_schema),
    )


def _unique_alias(candidate: str, used: set[str]) -> str:
    alias = candidate
    suffix = 2
    while alias in used:
        suffix_text = f"_{suffix}"
        alias = f"{candidate[: _MAX_PUBLIC_NAME_CHARACTERS - len(suffix_text)]}{suffix_text}"
        suffix += 1
    used.add(alias)
    return alias


def _auth_action(action: _BootstrapAuthAction | None) -> ConnectorAuthAction:
    if action is None:
        return "none"
    if action.type == "oauth":
        return "oauth"
    if action.type == "credentials_setup":
        return "credentials_setup"
    return "unknown"


def _bounded_diagnostics(value: JsonValue) -> tuple[str, ...]:
    values = value if isinstance(value, list) else [value]
    diagnostics: list[str] = []
    for raw in values:
        if len(diagnostics) == _MAX_DIAGNOSTICS_PER_CONNECTOR:
            break
        if not isinstance(raw, str):
            continue
        text = " ".join(raw.split()).strip()
        if not text:
            continue
        if text == "Connector failed to bootstrap." or re.fullmatch(
            r"Connector bootstrap issue: [a-z][a-z0-9_]{0,63}", text
        ):
            diagnostics.append(text)
            continue
        code, separator, _detail = text.partition(":")
        if separator and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
            diagnostic = f"Connector bootstrap issue: {code}"
        else:
            diagnostic = "Connector failed to bootstrap."
        diagnostics.append(diagnostic[:_MAX_DIAGNOSTIC_CHARACTERS])
    return tuple(diagnostics)


def _cache_entry(
    catalog: ResolvedConnectorCatalog, *, stored_at: int
) -> dict[str, object]:
    return {
        "format": _BOOTSTRAP_CACHE_FORMAT,
        "stored_at": stored_at,
        "payload": {
            "connectors": [
                _connector_cache_payload(item) for item in catalog.connectors
            ]
        },
    }


def _connector_cache_payload(connector: ResolvedConnector) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": connector.raw_id,
        "name": connector.display_name,
        "status": {"is_ready": connector.ready},
        "tools": [
            {
                "name": tool.raw_name,
                "description": tool.description,
                "inputSchema": dict(tool.input_schema),
            }
            for tool in connector.tools
        ],
    }
    if connector.auth_action != "none":
        payload["auth_action"] = {"type": connector.auth_action}
    if connector.diagnostics:
        payload["diagnostics"] = list(connector.diagnostics)
    return payload


def _parse_cache_entry(
    provider_fingerprint: str, raw_entry: object, *, now: int
) -> _CacheHit | None:
    if (
        not isinstance(raw_entry, dict)
        or _json_size(raw_entry) > _MAX_CACHE_ENTRY_BYTES
    ):
        return None
    cache_format = raw_entry.get("format")
    if cache_format is None:
        stored_at = raw_entry.get("stored_at_timestamp")
    elif cache_format == _BOOTSTRAP_CACHE_FORMAT:
        stored_at = raw_entry.get("stored_at")
    else:
        return None
    payload = raw_entry.get("payload")
    if not isinstance(stored_at, int) or not _is_fresh(stored_at, now=now):
        return None
    if not isinstance(payload, dict):
        return None

    normalized_payload = _normalize_cache_payload(payload)
    try:
        catalog = _resolve_catalog(normalized_payload, provider_fingerprint)
    except ConnectorCatalogValidationError:
        return None
    return _CacheHit(catalog=catalog, stored_at=stored_at)


def _normalize_cache_payload(payload: dict[object, object]) -> dict[str, object]:
    connectors = payload.get("connectors")
    if not isinstance(connectors, list):
        return {"connectors": connectors}
    normalized: list[object] = []
    for item in connectors:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        connector = dict(item)
        diagnostics = connector.pop("diagnostics", None)
        source_diagnostics = (
            diagnostics
            if diagnostics is not None
            else connector.get("bootstrap_errors")
        )
        if source_diagnostics is not None:
            connector["bootstrap_errors"] = source_diagnostics
        normalized.append(connector)
    return {"connectors": normalized}


def _connector_revision_payload(connector: ResolvedConnector) -> dict[str, object]:
    return {
        "raw_id": connector.raw_id,
        "alias": connector.alias,
        "display_name": connector.display_name,
        "ready": connector.ready,
        "auth_action": connector.auth_action,
        "tools": [
            {
                "raw_name": tool.raw_name,
                "description": tool.description,
                "input_schema": dict(tool.input_schema),
            }
            for tool in connector.tools
        ],
        "diagnostics": list(connector.diagnostics),
    }


def _is_fresh(stored_at: int, *, now: int) -> bool:
    if stored_at > now:
        return False
    return stored_at > now - _BOOTSTRAP_CACHE_TTL_SECONDS


def _json_size(value: object) -> int:
    try:
        return len(_canonical_json(value))
    except (TypeError, ValueError):
        return _MAX_CACHE_ENTRY_BYTES + 1


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _redacted_bootstrap_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"Failed to load workspace connectors (HTTP {exc.response.status_code})."
    return f"Failed to load workspace connectors: {type(exc).__name__}"


__all__ = [
    "ConnectorCatalogCache",
    "ConnectorCatalogError",
    "ConnectorCatalogReadResult",
    "ConnectorCatalogService",
    "ConnectorCatalogUnavailableError",
    "ConnectorCatalogValidationError",
    "connector_cache_fingerprint",
    "connector_source_enabled",
    "connector_tool_enabled",
    "normalize_connector_alias",
    "resolve_connector_selection",
]
