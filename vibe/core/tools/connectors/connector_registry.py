from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import os
import re
import time
from typing import TYPE_CHECKING, Any, ClassVar

import httpx

from vibe.core.paths import CONNECTOR_BOOTSTRAP_CACHE_FILE
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
)
from vibe.core.tools.remote import MCPTool, MCPToolResult, RemoteTool, _OpenArgs
from vibe.core.tools.ui import ToolResultDisplay
from vibe.core.types import ToolStreamEvent
from vibe.core.utils import run_sync
from vibe.observability.logging import logger
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context

if TYPE_CHECKING:
    from vibe.core.types import ToolResultEvent

_BOOTSTRAP_TIMEOUT = 30.0
_BOOTSTRAP_CACHE_TTL_SECONDS = 10 * 60
_SERVER_ERROR_STATUS = 500


async def call_tool_http(
    url: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
) -> MCPToolResult:
    from vibe.core.tools.mcp.tools import call_tool_http as call_mcp_tool_http

    return await call_mcp_tool_http(url, tool_name, arguments, headers=headers)


class ConnectorAuthAction(StrEnum):
    NONE = "none"
    OAUTH = "oauth"
    CREDENTIALS_SETUP = "credentials_setup"

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> ConnectorAuthAction:
        if not payload:
            return cls.NONE
        match payload.get("type"):
            case "oauth":
                return cls.OAUTH
            case "credentials_setup":
                return cls.CREDENTIALS_SETUP
            case _:
                return cls.NONE


@dataclass(frozen=True, slots=True)
class ConnectorToolDefinition:
    name: str
    description: str | None
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ConnectorCatalogEntry:
    connector_id: str
    alias: str
    display_name: str
    ready: bool
    auth_action: ConnectorAuthAction
    tools: tuple[ConnectorToolDefinition, ...]
    diagnostic: str | None = None


def _normalize_name(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    result = normalized.strip("_-")[:256]
    return result or "unnamed"


def _connector_tool_to_remote(tool: dict[str, Any]) -> RemoteTool | None:
    """Convert a bootstrap tool dict to a RemoteTool."""
    name = tool.get("name")
    if not name:
        return None
    return RemoteTool.model_validate({
        "name": name,
        "description": tool.get("description"),
        "inputSchema": tool.get("inputSchema") or {"type": "object", "properties": {}},
    })


_DEFAULT_BASE_URL = "https://api.mistral.ai"


def _bootstrap_cache_key(api_key: str, server_url: str | None) -> str:
    base_url = server_url or _DEFAULT_BASE_URL
    return hashlib.sha256(f"{base_url}\0{api_key}".encode()).hexdigest()


def _strip_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_none(item) for key, item in value.items() if item is not None
        }
    if isinstance(value, list):
        return [_strip_none(item) for item in value]
    return value


def _read_bootstrap_cache_entries() -> dict[str, Any]:
    try:
        with CONNECTOR_BOOTSTRAP_CACHE_FILE.path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_bootstrap_cache_entries(entries: dict[str, Any]) -> None:
    cache_path = CONNECTOR_BOOTSTRAP_CACHE_FILE.path
    tmp_path = cache_path.with_name(f".{cache_path.name}.{os.getpid()}.tmp")
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(entries, f, separators=(",", ":"))
        os.replace(tmp_path, cache_path)
    except (OSError, TypeError):
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        logger.debug(
            "Failed to write connector bootstrap cache file %s",
            cache_path,
            exc_info=True,
        )


def _is_fresh_bootstrap_cache_entry(entry: Any, now: int) -> bool:
    if not isinstance(entry, dict):
        return False
    stored_at = entry.get("stored_at_timestamp")
    payload = entry.get("payload")
    if not isinstance(stored_at, int) or not isinstance(payload, dict):
        return False
    if stored_at <= now - _BOOTSTRAP_CACHE_TTL_SECONDS:
        return False
    return isinstance(payload.get("connectors"), list)


def _tool_bootstrap_cache_payload(tool: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "name": tool.get("name"),
        "description": tool.get("description"),
        "inputSchema": tool.get("inputSchema"),
    }
    return _strip_none(payload)


def _connector_bootstrap_cache_payload(connector: dict[str, Any]) -> dict[str, Any]:
    auth_action = connector.get("auth_action")
    status = connector.get("status") or {}
    payload: dict[str, Any] = {
        "id": connector.get("id"),
        "name": connector.get("name"),
        "status": {"is_ready": bool(status.get("is_ready", False))},
        "tools": [
            _tool_bootstrap_cache_payload(tool)
            for tool in connector.get("tools") or []
            if isinstance(tool, dict)
        ],
    }
    if isinstance(auth_action, dict):
        payload["auth_action"] = {"type": auth_action.get("type")}
    # bootstrap_errors is consumed (surfaced to the user), so persist it; a warm
    # cache hit must rebuild the per-connector error, not silently drop it.
    if bootstrap_errors := connector.get("bootstrap_errors"):
        payload["bootstrap_errors"] = bootstrap_errors
    return _strip_none(payload)


def _bootstrap_cache_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "connectors": [
            _connector_bootstrap_cache_payload(connector)
            for connector in payload.get("connectors") or []
            if isinstance(connector, dict)
        ]
    }


def _format_http_status_error(
    exc: httpx.HTTPStatusError, connector_name: str, connector_id: str
) -> str:
    """Format an HTTP status error with response body for debugging."""
    status = exc.response.status_code
    connector_ref = f"'{connector_name}' (id: {connector_id})"
    try:
        body = exc.response.text[:500]
    except Exception:
        body = ""

    match status:
        case 401 | 403:
            detail = (
                f"Connector {connector_ref} authentication failed "
                f"(HTTP {status}). Check your MISTRAL_API_KEY."
            )
        case 404:
            detail = (
                f"Connector {connector_ref} not found (HTTP 404). "
                "It may have been deleted or is not accessible."
            )
        case _:
            detail = f"Connector {connector_ref} request failed (HTTP {status})."

    if body:
        detail += f"\nServer response: {body}"
    return detail


def _format_connector_bootstrap_errors(bootstrap_errors: Any) -> str:
    """Flatten a connector's per-bootstrap error payload into readable text."""
    if isinstance(bootstrap_errors, list):
        parts = [str(item).strip() for item in bootstrap_errors if str(item).strip()]
        if parts:
            return "; ".join(parts)
    text = str(bootstrap_errors).strip()
    return text or "Connector failed to bootstrap."


def _bootstrap_error_message(exc: Exception) -> str:
    """Return the real backend failure so developers can diagnose it."""
    if http_err := _unwrap_http_status_error(exc):
        status = http_err.response.status_code
        try:
            body = http_err.response.text[:500].strip()
        except Exception:
            body = ""
        detail = f"Failed to load workspace connectors (HTTP {status})."
        if body:
            detail += f"\nServer response: {body}"
        if status >= _SERVER_ERROR_STATUS:
            detail += (
                "\nThis looks like a temporary server issue — "
                "retry with `/reload` in a few minutes."
            )
        return detail
    message = str(exc).strip()
    suffix = f": {message}" if message else ""
    return f"Failed to load workspace connectors: {type(exc).__name__}{suffix}"


def _unwrap_http_status_error(exc: Exception) -> httpx.HTTPStatusError | None:
    """Extract an HTTPStatusError from an exception or ExceptionGroup."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc
    if isinstance(exc, ExceptionGroup):
        for inner in exc.exceptions:
            if found := _unwrap_http_status_error(inner):
                return found
    if cause := exc.__cause__:
        if isinstance(cause, httpx.HTTPStatusError):
            return cause
    return None


def _connector_error_message(
    exc: Exception, connector_id: str, connector_name: str
) -> str:
    """Return an actionable error message for connector proxy failures."""
    if http_err := _unwrap_http_status_error(exc):
        return _format_http_status_error(http_err, connector_name, connector_id)
    if isinstance(exc, httpx.TimeoutException):
        return (
            f"Connector '{connector_name}' timed out. "
            "The remote service may be slow or unreachable."
        )
    if isinstance(exc, httpx.ConnectError):
        return (
            f"Cannot reach connector proxy for '{connector_name}'. "
            "Check your network connection."
        )
    if isinstance(exc, ExceptionGroup):
        messages = [str(e) for e in exc.exceptions]
        return (
            f"Connector '{connector_name}' call failed with multiple errors: "
            + "; ".join(messages)
        )
    return f"Connector '{connector_name}' call failed: {exc}"


def create_connector_proxy_tool_class(
    *,
    connector_name: str,
    connector_alias: str,
    connector_id: str,
    remote: RemoteTool,
    api_key: str,
    server_url: str | None = None,
) -> type[BaseTool[_OpenArgs, MCPToolResult, BaseToolConfig, BaseToolState]]:
    alias = connector_alias
    published_name = f"connector_{alias}_{remote.name}"
    base_url = server_url or _DEFAULT_BASE_URL

    class ConnectorProxyTool(MCPTool):
        description: ClassVar[str] = f"[{alias}] " + (
            remote.description or f"Connector tool '{remote.name}'"
        )
        _server_name: ClassVar[str] = alias
        _remote_name: ClassVar[str] = remote.name
        _input_schema: ClassVar[dict[str, Any]] = remote.input_schema
        _is_connector: ClassVar[bool] = True
        _connector_id: ClassVar[str] = connector_id
        _connector_name: ClassVar[str] = connector_name
        _api_key: ClassVar[str] = api_key
        _base_url: ClassVar[str] = base_url

        @classmethod
        def get_name(cls) -> str:
            return published_name

        @classmethod
        def get_parameters(cls) -> dict[str, Any]:
            return dict(cls._input_schema)

        async def run(
            self, args: _OpenArgs, ctx: InvokeContext | None = None
        ) -> AsyncGenerator[ToolStreamEvent | MCPToolResult, None]:
            url = f"{self._base_url}/v1/connectors-gateway/{self._connector_id}/mcp"
            headers = {"Authorization": f"Bearer {self._api_key}"}
            payload = args.model_dump(exclude_none=True)
            try:
                yield await call_tool_http(
                    url, self._remote_name, payload, headers=headers
                )
            except Exception as exc:
                msg = _connector_error_message(
                    exc, self._connector_id, self._connector_name
                )
                raise ToolError(msg) from exc

        @classmethod
        def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
            if not isinstance(event.result, MCPToolResult):
                return ToolResultDisplay(
                    success=False,
                    message=event.error or event.skip_reason or "No result",
                )
            return ToolResultDisplay(
                success=event.result.ok,
                verb="Ran",
                message=f"connector {event.result.tool}",
            )

        @classmethod
        def get_status_text(cls) -> str:
            return f"Calling connector tool {remote.name}"

    ConnectorProxyTool.__name__ = f"Connector_{alias}__{remote.name}"
    return ConnectorProxyTool


def _deduplicate_connectors(
    connectors: list[dict[str, Any]],
) -> list[tuple[str, str, dict[str, Any]]]:
    """Deduplicate connectors by normalized alias, preserving order.

    When two connectors share the same alias, disambiguate with a
    numeric suffix rather than silently dropping.
    """
    seen_names: set[str] = set()
    result: list[tuple[str, str, dict[str, Any]]] = []
    for connector in connectors:
        connector_id = str(connector.get("id", ""))
        if not connector_id:
            continue
        raw_name = connector.get("name") or connector_id
        alias = _normalize_name(raw_name)
        if alias in seen_names:
            original = alias
            suffix = 2
            while f"{original}_{suffix}" in seen_names:
                suffix += 1
            alias = f"{original}_{suffix}"
            logger.warning(
                "Connector %r alias %r collides, using %r", raw_name, original, alias
            )
        seen_names.add(alias)
        result.append((connector_id, alias, connector))
    return result


class ConnectorRegistry:
    """Discovers connector tools from the Mistral API.

    Fetches all connectors and their tools on first call, then caches.
    """

    def __init__(
        self,
        api_key: str,
        server_url: str | None = None,
        *,
        catalog_entries: tuple[ConnectorCatalogEntry, ...] | None = None,
    ) -> None:
        self._api_key = api_key
        self._server_url = server_url
        self._bootstrap_cache_key = _bootstrap_cache_key(api_key, server_url)
        self._cache: dict[str, dict[str, type[BaseTool]]] | None = None
        self._connector_names: list[str] = []
        self._connector_connected: dict[str, bool] = {}
        self._connector_auth_action: dict[str, ConnectorAuthAction] = {}
        self._alias_to_id: dict[str, str] = {}
        self._bootstrap_error: str | None = None
        self._connector_errors: dict[str, str] = {}
        self._discover_lock = asyncio.Lock()
        self._host_managed = catalog_entries is not None
        self._host_catalog_entries = catalog_entries
        if catalog_entries is not None:
            self._accept_catalog(catalog_entries)

    def clone_configuration(self) -> ConnectorRegistry:
        return ConnectorRegistry(
            api_key=self._api_key,
            server_url=self._server_url,
            catalog_entries=self._host_catalog_entries,
        )

    def reconfigure(self, entries: tuple[ConnectorCatalogEntry, ...]) -> None:
        self._host_managed = True
        self._host_catalog_entries = entries
        self._accept_catalog(entries)

    def _accept_catalog(self, entries: tuple[ConnectorCatalogEntry, ...]) -> None:
        cache: dict[str, dict[str, type[BaseTool]]] = {}
        connector_names: list[str] = []
        connector_connected: dict[str, bool] = {}
        connector_auth_action: dict[str, ConnectorAuthAction] = {}
        connector_errors: dict[str, str] = {}
        alias_to_id: dict[str, str] = {}

        for entry in entries:
            connector_names.append(entry.alias)
            connector_auth_action[entry.alias] = entry.auth_action
            alias_to_id[entry.alias] = entry.connector_id
            if entry.diagnostic is not None:
                connector_errors[entry.alias] = entry.diagnostic
            if not entry.ready:
                connector_connected[entry.alias] = False
                continue
            tools = self._build_tools_for_connector(
                connector_id=entry.connector_id,
                alias=entry.alias,
                name=entry.display_name,
                raw_tools=[
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.input_schema,
                    }
                    for tool in entry.tools
                ],
            )
            cache[entry.connector_id] = tools
            connector_connected[entry.alias] = bool(tools)

        self._connector_names = connector_names
        self._connector_connected = connector_connected
        self._connector_auth_action = connector_auth_action
        self._connector_errors = connector_errors
        self._alias_to_id = alias_to_id
        self._cache = cache

    def get_tools(self, *, force_refresh: bool = False) -> dict[str, type[BaseTool]]:
        """Return proxy tool classes for all connectors, using cache when possible."""
        return run_sync(self.get_tools_async(force_refresh=force_refresh))

    async def get_tools_async(
        self, *, force_refresh: bool = False
    ) -> dict[str, type[BaseTool]]:
        """Return proxy tool classes for all connectors, using cache when possible."""
        if self._cache is not None and not force_refresh:
            result: dict[str, type[BaseTool]] = {}
            for tools in self._cache.values():
                result.update(tools)
            return result

        return await self._discover_all(force_refresh=force_refresh)

    async def _fetch_bootstrap(self) -> dict[str, Any]:
        base_url = self._server_url or _DEFAULT_BASE_URL
        url = f"{base_url}/v1/connectors/bootstrap"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        params = {"include_auth_actionable_connectors": "true"}
        async with VibeAsyncHTTPClient(
            timeout=_BOOTSTRAP_TIMEOUT, verify=build_ssl_context()
        ) as http:
            response = await http.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()

    def _read_cached_bootstrap(self) -> dict[str, Any] | None:
        entry = _read_bootstrap_cache_entries().get(self._bootstrap_cache_key)
        if not isinstance(entry, dict):
            return None

        stored_at = entry.get("stored_at_timestamp")
        payload = entry.get("payload")
        if not isinstance(stored_at, int) or not isinstance(payload, dict):
            return None
        if stored_at <= int(time.time()) - _BOOTSTRAP_CACHE_TTL_SECONDS:
            return None
        if not isinstance(payload.get("connectors"), list):
            return None
        return payload

    def _write_cached_bootstrap(self, payload: dict[str, Any]) -> None:
        now = int(time.time())
        entries = _read_bootstrap_cache_entries()
        entries = {
            key: entry
            for key, entry in entries.items()
            if _is_fresh_bootstrap_cache_entry(entry, now)
        }
        entries[self._bootstrap_cache_key] = {
            "stored_at_timestamp": now,
            "payload": _bootstrap_cache_payload(payload),
        }
        _write_bootstrap_cache_entries(entries)

    def _build_tools_for_connector(
        self,
        *,
        connector_id: str,
        alias: str,
        name: str,
        raw_tools: list[dict[str, Any]],
    ) -> dict[str, type[BaseTool]]:
        tools_map: dict[str, type[BaseTool]] = {}
        for tool in raw_tools:
            remote = _connector_tool_to_remote(tool)
            if remote is None:
                continue
            try:
                proxy_cls = create_connector_proxy_tool_class(
                    connector_name=name,
                    connector_alias=alias,
                    connector_id=connector_id,
                    remote=remote,
                    api_key=self._api_key,
                    server_url=self._server_url,
                )
                tools_map[proxy_cls.get_name()] = proxy_cls
            except Exception:
                logger.warning(
                    "Failed to register connector tool %s for %s",
                    tool.get("name", "<unknown>"),
                    name,
                    exc_info=True,
                )
        return tools_map

    async def _discover_all(
        self, *, force_refresh: bool = False
    ) -> dict[str, type[BaseTool]]:
        if self._host_managed:
            return self._all_cached_tools()
        async with self._discover_lock:
            # Re-check under lock — another coroutine may have finished
            # discovery while we waited.
            if self._cache is not None and not force_refresh:
                result: dict[str, type[BaseTool]] = {}
                for tools in self._cache.values():
                    result.update(tools)
                return result

            data = None if force_refresh else self._read_cached_bootstrap()
            try:
                if data is None:
                    data = await self._fetch_bootstrap()
                    self._write_cached_bootstrap(data)
            except Exception as exc:
                logger.warning("Failed to bootstrap connectors", exc_info=True)
                self._bootstrap_error = _bootstrap_error_message(exc)
                # Leave the cache unset (None, not {}) so the next integration —
                # e.g. triggered by `/reload` — actually re-hits the endpoint
                # instead of short-circuiting on an empty cache.
                self._cache = None
                self._connector_names = []
                self._connector_connected = {}
                self._connector_auth_action = {}
                self._connector_errors = {}
                self._alias_to_id = {}
                return {}

            self._bootstrap_error = None

            unique_connectors = _deduplicate_connectors(data.get("connectors") or [])

            cache: dict[str, dict[str, type[BaseTool]]] = {}
            all_tools: dict[str, type[BaseTool]] = {}
            connector_names: list[str] = []
            connector_connected: dict[str, bool] = {}
            connector_auth_action: dict[str, ConnectorAuthAction] = {}
            connector_errors: dict[str, str] = {}

            for connector_id, alias, connector in unique_connectors:
                connector_names.append(alias)
                name = connector.get("name") or connector_id
                auth_action = ConnectorAuthAction.from_payload(
                    connector.get("auth_action")
                )
                connector_auth_action[alias] = auth_action

                if bootstrap_errors := connector.get("bootstrap_errors"):
                    logger.warning(
                        "Connector %r bootstrap errors: %s", name, bootstrap_errors
                    )
                    connector_errors[alias] = _format_connector_bootstrap_errors(
                        bootstrap_errors
                    )

                status = connector.get("status") or {}
                if not status.get("is_ready", False):
                    connector_connected[alias] = False
                    continue

                tools_map = self._build_tools_for_connector(
                    connector_id=connector_id,
                    alias=alias,
                    name=name,
                    raw_tools=connector.get("tools") or [],
                )
                cache[connector_id] = tools_map
                all_tools.update(tools_map)
                connector_connected[alias] = bool(tools_map)

            # Publish atomically — concurrent callers waiting on the
            # lock will see the completed cache.
            self._connector_names = connector_names
            self._connector_connected = connector_connected
            self._connector_auth_action = connector_auth_action
            self._connector_errors = connector_errors
            self._alias_to_id = {alias: cid for cid, alias, _ in unique_connectors}
            self._cache = cache

            return all_tools

    def _all_cached_tools(self) -> dict[str, type[BaseTool]]:
        result: dict[str, type[BaseTool]] = {}
        if self._cache is None:
            return result
        for tools in self._cache.values():
            result.update(tools)
        return result

    @property
    def connector_count(self) -> int:
        if self._cache is None:
            return 0
        return len(self._connector_names)

    def get_connector_names(self) -> list[str]:
        return list(self._connector_names)

    def get_catalog_tools(self, alias: str) -> tuple[ConnectorToolDefinition, ...]:
        entries = self._host_catalog_entries
        if entries is None:
            return ()
        entry = next((item for item in entries if item.alias == alias), None)
        return entry.tools if entry is not None else ()

    def bootstrap_error(self) -> str | None:
        return self._bootstrap_error

    def connector_error_for(self, alias: str) -> str | None:
        return self._connector_errors.get(alias)

    def is_connected(self, name: str) -> bool:
        return self._connector_connected.get(name, False)

    def get_auth_action(self, alias: str) -> ConnectorAuthAction:
        return self._connector_auth_action.get(alias, ConnectorAuthAction.NONE)

    def get_connector_id(self, alias: str) -> str | None:
        """Return the API connector ID for a given alias, or None."""
        return self._alias_to_id.get(alias)

    async def refresh_connector_async(self, alias: str) -> dict[str, type[BaseTool]]:
        """Re-fetch tools for a single connector by alias.

        Calls the bootstrap endpoint and extracts the matching connector.
        Updates the internal cache for that connector only. Returns
        the new tool map (empty dict on failure).
        """
        connector_id = self._alias_to_id.get(alias)
        if connector_id is None:
            return {}
        if self._host_managed:
            return dict((self._cache or {}).get(connector_id, {}))

        tools_map: dict[str, type[BaseTool]] | None = None
        fresh_auth_action: ConnectorAuthAction | None = None
        fresh_error: str | None = None
        found = False
        fetch_ok = False
        try:
            data = await self._fetch_bootstrap()
            fetch_ok = True
            self._write_cached_bootstrap(data)
            for connector in data.get("connectors") or []:
                if str(connector.get("id")) != connector_id:
                    continue

                found = True
                name = connector.get("name") or connector_id
                fresh_auth_action = ConnectorAuthAction.from_payload(
                    connector.get("auth_action")
                )
                if bootstrap_errors := connector.get("bootstrap_errors"):
                    fresh_error = _format_connector_bootstrap_errors(bootstrap_errors)
                status = connector.get("status") or {}
                if not status.get("is_ready", False):
                    break

                tools_map = self._build_tools_for_connector(
                    connector_id=connector_id,
                    alias=alias,
                    name=name,
                    raw_tools=connector.get("tools") or [],
                )
                break
        except Exception:
            logger.debug("Failed to refresh connector %s", alias)

        if self._cache is None:
            self._cache = {}

        if fetch_ok and not found:
            self._drop_connector(alias, connector_id)
            return {}

        if fresh_auth_action is not None:
            self._connector_auth_action[alias] = fresh_auth_action

        if fetch_ok and found:
            if fresh_error is not None:
                self._connector_errors[alias] = fresh_error
            else:
                self._connector_errors.pop(alias, None)

        if tools_map is None:
            self._cache.pop(connector_id, None)
            self._connector_connected[alias] = False
            return {}

        self._cache[connector_id] = tools_map
        self._connector_connected[alias] = bool(tools_map)
        return tools_map

    def _drop_connector(self, alias: str, connector_id: str) -> None:
        if self._cache is not None:
            self._cache.pop(connector_id, None)
        self._connector_connected.pop(alias, None)
        self._connector_auth_action.pop(alias, None)
        self._connector_errors.pop(alias, None)
        self._alias_to_id.pop(alias, None)
        if alias in self._connector_names:
            self._connector_names.remove(alias)

    async def get_auth_url(self, alias: str) -> str | None:
        """Return the OAuth authorization URL for a connector, or None.

        Returns None when the connector does not support OAuth or the
        alias is unknown.
        """
        connector_id = self._alias_to_id.get(alias)
        if connector_id is None:
            return None
        try:
            http_client = VibeAsyncHTTPClient(
                verify=build_ssl_context(), follow_redirects=True
            )
            try:
                from mistralai.client import Mistral

                sdk_client = Mistral(
                    api_key=self._api_key,
                    server_url=self._server_url,
                    async_client=http_client,
                )
                async with sdk_client as client:
                    result = await client.beta.connectors.get_auth_url_async(
                        connector_id_or_name=connector_id
                    )
                return result.auth_url
            finally:
                await http_client.aclose()
        except Exception:
            logger.debug("Failed to get auth URL for connector %s", alias)
            return None

    def clear(self) -> None:
        if self._host_catalog_entries is not None:
            self._accept_catalog(self._host_catalog_entries)
            return
        self._cache = None
        self._connector_names = []
        self._connector_connected = {}
        self._connector_auth_action = {}
        self._alias_to_id = {}
