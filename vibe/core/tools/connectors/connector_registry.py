from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any, ClassVar

from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState
from vibe.core.tools.remote import MCPTool, MCPToolResult, RemoteTool, _OpenArgs
from vibe.observability.logging import logger


class ConnectorAuthAction(StrEnum):
    NONE = "none"
    OAUTH = "oauth"
    CREDENTIALS_SETUP = "credentials_setup"
    NO_AUTH_CONNECT = "no_auth_connect"


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


# Sent as a repeated query param; a comma-joined string is read as one bogus name.
# Off-by-default: gated builtins (e.g. document_library) are added only when a
# caller opts in via ``builtin_connectors``; this is the safe fallback set so any
# caller that omits ``builtins=`` never silently re-exposes a gated connector.
_DEFAULT_BUILTIN_CONNECTORS = ("web_search",)


def builtin_connectors(
    *, enable_document_library_connector: bool, enable_mistral_mcp_connector: bool
) -> tuple[str, ...]:
    """Builtin connectors to request from bootstrap, gated by experimental flags.

    ``document_library`` is withheld unless explicitly enabled so its (currently
    unstable) MCP tools are not surfaced by default. ``mistral_mcp`` is rolled
    out through a GrowthBook experiment.
    """
    connectors = _DEFAULT_BUILTIN_CONNECTORS
    if enable_document_library_connector:
        connectors += ("document_library",)
    if enable_mistral_mcp_connector:
        connectors += ("mistral_mcp",)
    return connectors


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


class ConnectorRegistry:
    """Connector proxy tools for the catalog the app server resolved.

    The app server owns fetching and caching the catalog
    (``SharedConnectorCatalog``); this only projects the entries it is given.
    """

    def __init__(self, catalog_entries: tuple[ConnectorCatalogEntry, ...] = ()) -> None:
        self._cache: dict[str, dict[str, type[BaseTool]]] = {}
        self._connector_names: list[str] = []
        self._connector_connected: dict[str, bool] = {}
        self._connector_auth_action: dict[str, ConnectorAuthAction] = {}
        self._alias_to_id: dict[str, str] = {}
        self._connector_errors: dict[str, str] = {}
        self._catalog_entries = catalog_entries
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
        del force_refresh
        result: dict[str, type[BaseTool]] = {}
        for tools in self._cache.values():
            result.update(tools)
        return result

    async def get_tools_async(
        self, *, force_refresh: bool = False
    ) -> dict[str, type[BaseTool]]:
        return self.get_tools(force_refresh=force_refresh)

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

    @property
    def connector_count(self) -> int:
        return len(self._connector_names)

    def get_connector_names(self) -> list[str]:
        return list(self._connector_names)

    def get_catalog_tools(self, alias: str) -> tuple[ConnectorToolDefinition, ...]:
        entry = next(
            (item for item in self._catalog_entries if item.alias == alias), None
        )
        return entry.tools if entry is not None else ()

    def connector_error_for(self, alias: str) -> str | None:
        return self._connector_errors.get(alias)

    def is_connected(self, name: str) -> bool:
        return self._connector_connected.get(name, False)

    def get_auth_action(self, alias: str) -> ConnectorAuthAction:
        return self._connector_auth_action.get(alias, ConnectorAuthAction.NONE)

    def clear(self) -> None:
        self._accept_catalog(self._catalog_entries)
