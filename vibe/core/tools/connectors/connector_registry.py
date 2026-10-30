from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import os
import re
import time
from typing import Any, ClassVar

import httpx2

from vibe.core.paths import CONNECTOR_BOOTSTRAP_CACHE_FILE
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState
from vibe.core.tools.remote import MCPTool, MCPToolResult, RemoteTool, _OpenArgs
from vibe.core.utils import run_sync
from vibe.core.utils.exceptions import first_of_type
from vibe.observability.logging import logger
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context

_BOOTSTRAP_TIMEOUT = 30.0
_BOOTSTRAP_CACHE_TTL_SECONDS = 10 * 60
_SERVER_ERROR_STATUS = 500


class ConnectorAuthAction(StrEnum):
    NONE = "none"
    OAUTH = "oauth"
    CREDENTIALS_SETUP = "credentials_setup"
    NO_AUTH_CONNECT = "no_auth_connect"

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> ConnectorAuthAction:
        if not payload:
            return cls.NONE
        match payload.get("type"):
            case "oauth":
                return cls.OAUTH
            case "credentials_setup":
                return cls.CREDENTIALS_SETUP
            case "no_auth_connect":
                return cls.NO_AUTH_CONNECT
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
# Sent as a repeated query param; a comma-joined string is read as one bogus name.
# Off-by-default: gated builtins (e.g. document_library) are added only when a
# caller opts in via ``builtin_connectors``; this is the safe fallback set so any
# caller that omits ``builtins=`` never silently re-exposes a gated connector.
_DEFAULT_BUILTIN_CONNECTORS = ("web_search",)


def builtin_connectors(*, enable_document_library_connector: bool) -> tuple[str, ...]:
    """Builtin connectors to request from bootstrap, gated by experimental flags.

    ``document_library`` is withheld unless explicitly enabled so its (currently
    unstable) MCP tools are not surfaced by default.
    """
    connectors = _DEFAULT_BUILTIN_CONNECTORS
    if enable_document_library_connector:
        connectors += ("document_library",)
    return connectors


def _bootstrap_cache_key(
    api_key: str,
    server_url: str | None,
    builtins: tuple[str, ...] = _DEFAULT_BUILTIN_CONNECTORS,
) -> str:
    base_url = server_url or _DEFAULT_BASE_URL
    builtins_key = ",".join(builtins)
    return hashlib.sha256(
        f"{base_url}\0{api_key}\0supports_mcp=true\0{builtins_key}".encode()
    ).hexdigest()


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
        "protocol": connector.get("protocol"),
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
        detail = f"Failed to load connectors (HTTP {status})."
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
    return f"Failed to load connectors: {type(exc).__name__}{suffix}"


def _unwrap_http_status_error(exc: Exception) -> httpx2.HTTPStatusError | None:
    """Extract an HTTPStatusError from an exception or ExceptionGroup."""
    if found := first_of_type(exc, httpx2.HTTPStatusError):
        return found
    if isinstance(exc.__cause__, httpx2.HTTPStatusError):
        return exc.__cause__
    return None


def create_connector_proxy_tool_class(
    *, connector_alias: str, remote: RemoteTool
) -> type[BaseTool[_OpenArgs, MCPToolResult, BaseToolConfig, BaseToolState]]:
    alias = connector_alias
    published_name = f"connector_{alias}_{remote.name}"

    class ConnectorProxyTool(MCPTool):
        description: ClassVar[str] = f"[{alias}] " + (
            remote.description or f"Connector tool '{remote.name}'"
        )
        _server_name: ClassVar[str] = alias
        _remote_name: ClassVar[str] = remote.name
        _input_schema: ClassVar[dict[str, Any]] = remote.input_schema
        _is_connector: ClassVar[bool] = True

        @classmethod
        def get_name(cls) -> str:
            return published_name

        @classmethod
        def get_parameters(cls) -> dict[str, Any]:
            return dict(cls._input_schema)

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
        builtins: tuple[str, ...] = _DEFAULT_BUILTIN_CONNECTORS,
    ) -> None:
        self._api_key = api_key
        self._server_url = server_url
        self._builtins = builtins
        self._bootstrap_cache_key = _bootstrap_cache_key(api_key, server_url, builtins)
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
        params = {
            "include_auth_actionable_connectors": "true",
            "builtin_connectors": self._builtins,
            "supports_mcp": "true",
        }
        async with VibeAsyncHTTPClient(
            timeout=_BOOTSTRAP_TIMEOUT, verify=build_ssl_context()
        ) as http:
            response = await http.get(url, headers=headers, params=params)
            response.raise_for_status()
            return response.json()

    def _read_cached_bootstrap(self) -> dict[str, Any] | None:
        entry = _read_bootstrap_cache_entries().get(self._bootstrap_cache_key)
        if not _is_fresh_bootstrap_cache_entry(entry, int(time.time())):
            return None
        if not isinstance(entry, dict):
            return None
        payload = entry.get("payload")
        return payload if isinstance(payload, dict) else None

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
        self, *, alias: str, name: str, raw_tools: list[dict[str, Any]]
    ) -> dict[str, type[BaseTool]]:
        tools_map: dict[str, type[BaseTool]] = {}
        for tool in raw_tools:
            remote = _connector_tool_to_remote(tool)
            if remote is None:
                continue
            try:
                proxy_cls = create_connector_proxy_tool_class(
                    connector_alias=alias, remote=remote
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

            connectors = [
                connector
                for connector in data.get("connectors") or []
                if isinstance(connector, dict)
            ]
            unique_connectors = _deduplicate_connectors(connectors)

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
                    alias=alias, name=name, raw_tools=connector.get("tools") or []
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

    def clear(self) -> None:
        if self._host_catalog_entries is not None:
            self._accept_catalog(self._host_catalog_entries)
            return
        self._cache = None
        self._connector_names = []
        self._connector_connected = {}
        self._connector_auth_action = {}
        self._alias_to_id = {}
