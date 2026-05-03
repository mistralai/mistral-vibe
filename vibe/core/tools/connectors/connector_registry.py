from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import re
from typing import TYPE_CHECKING, Any, ClassVar

import httpx
from mistralai.client import Mistral
from mistralai.client.models.connectorsqueryfilters import (
    ConnectorsQueryFiltersTypedDict,
)

from vibe.core.logger import logger
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
)
from vibe.core.tools.mcp.tools import (
    MCPTool,
    MCPToolResult,
    RemoteTool,
    _OpenArgs,
    call_tool_http,
)
from vibe.core.tools.ui import ToolResultDisplay
from vibe.core.types import ToolStreamEvent
from vibe.core.utils import run_sync

if TYPE_CHECKING:
    from vibe.core.types import ToolResultEvent

_LIST_PAGE_SIZE = 100
_LIST_QUERY_FILTERS: ConnectorsQueryFiltersTypedDict = {"active": True}
_TOOL_FETCH_TIMEOUT = 8.0


def _normalize_name(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    result = normalized.strip("_-")[:256]
    return result or "unnamed"


def _connector_tool_to_remote(tool: dict[str, Any] | Any) -> RemoteTool | None:
    """Convert a ConnectorTool (SDK object or raw dict) to a RemoteTool."""
    if isinstance(tool, dict):
        name = tool.get("name")
        description = tool.get("description")
        schema = tool.get("jsonschema") or tool.get("json_schema")
    else:
        name = getattr(tool, "name", None)
        description = getattr(tool, "description", None)
        schema = getattr(tool, "jsonschema", None) or getattr(tool, "json_schema", None)
    if not name:
        return None
    if schema is not None and not isinstance(schema, dict):
        dump = getattr(schema, "model_dump", None)
        schema = dump() if callable(dump) else None
    return RemoteTool.model_validate({
        "name": name,
        "description": description,
        "inputSchema": schema or {"type": "object", "properties": {}},
    })


_DEFAULT_BASE_URL = "https://api.mistral.ai"


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
            url = (
                f"{self._base_url}/v1/experimental/connectors/{self._connector_id}/mcp"
            )
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
            message = f"Connector tool {event.result.tool} completed"
            return ToolResultDisplay(success=event.result.ok, message=message)

        @classmethod
        def get_status_text(cls) -> str:
            return f"Calling connector tool {remote.name}"

    ConnectorProxyTool.__name__ = f"Connector_{alias}__{remote.name}"
    return ConnectorProxyTool


class ConnectorRegistry:
    """Discovers connector tools from the Mistral API.

    Fetches all connectors and their tools on first call, then caches.
    """

    def __init__(self, api_key: str, server_url: str | None = None) -> None:
        self._api_key = api_key
        self._server_url = server_url
        self._cache: dict[str, dict[str, type[BaseTool]]] | None = None
        self._connector_names: list[str] = []
        self._connector_connected: dict[str, bool] = {}
        self._alias_to_id: dict[str, str] = {}
        self._discover_lock = asyncio.Lock()

    def _get_client(self) -> Mistral:
        return Mistral(api_key=self._api_key, server_url=self._server_url)

    def get_tools(self) -> dict[str, type[BaseTool]]:
        """Return proxy tool classes for all connectors, using cache when possible."""
        return run_sync(self.get_tools_async())

    async def get_tools_async(self) -> dict[str, type[BaseTool]]:
        """Return proxy tool classes for all connectors, using cache when possible."""
        if self._cache is not None:
            result: dict[str, type[BaseTool]] = {}
            for tools in self._cache.values():
                result.update(tools)
            return result

        return await self._discover_all()

    async def _discover_all(self) -> dict[str, type[BaseTool]]:
        async with self._discover_lock:
            # Re-check under lock — another coroutine may have finished
            # discovery while we waited.
            if self._cache is not None:
                result: dict[str, type[BaseTool]] = {}
                for tools in self._cache.values():
                    result.update(tools)
                return result

            async with self._get_client() as client:
                connectors: list[Any] = []

                try:
                    page = await client.beta.connectors.list_async(
                        page_size=_LIST_PAGE_SIZE, query_filters=_LIST_QUERY_FILTERS
                    )
                    connectors.extend(page.items or [])
                    while page.pagination and page.pagination.next_cursor:
                        page = await client.beta.connectors.list_async(
                            page_size=_LIST_PAGE_SIZE,
                            cursor=page.pagination.next_cursor,
                            query_filters=_LIST_QUERY_FILTERS,
                        )
                        connectors.extend(page.items or [])
                except Exception as exc:
                    logger.warning(f"Failed to list connectors: {exc}")
                    self._cache = {}
                    self._connector_names = []
                    self._connector_connected = {}
                    self._alias_to_id = {}
                    return {}

                # Build results locally to avoid publishing incomplete state.
                cache: dict[str, dict[str, type[BaseTool]]] = {}
                all_tools: dict[str, type[BaseTool]] = {}
                connector_names: list[str] = []
                connector_connected: dict[str, bool] = {}

                # Deduplicate by normalized name, preserving order.
                # When two connectors share the same alias, disambiguate
                # with a numeric suffix rather than silently dropping.
                seen_names: set[str] = set()
                unique_connectors: list[tuple[str, str, Any]] = []
                for connector in connectors:
                    connector_id = str(connector.id)
                    raw_name = connector.name or connector_id
                    alias = _normalize_name(raw_name)
                    if alias in seen_names:
                        original = alias
                        suffix = 2
                        while f"{alias}_{suffix}" in seen_names:
                            suffix += 1
                        alias = f"{alias}_{suffix}"
                        logger.warning(
                            f"Connector {raw_name!r} alias {original!r} collides, "
                            f"using {alias!r}"
                        )
                    seen_names.add(alias)
                    unique_connectors.append((connector_id, alias, connector))

                # Fetch tools for all connectors in parallel.
                tasks = [
                    self._fetch_connector_tools(client, c, alias)
                    for _, alias, c in unique_connectors
                ]
                results = await asyncio.gather(*tasks)

                for (connector_id, alias, _), tools_map in zip(
                    unique_connectors, results, strict=True
                ):
                    connector_names.append(alias)
                    if tools_map is None:
                        # Timeout or error — show in UI but don't cache so
                        # a refresh will retry discovery for this connector.
                        connector_connected[alias] = False
                        continue
                    cache[connector_id] = tools_map
                    all_tools.update(tools_map)
                    # TODO: replace with actual API field when available
                    connector_connected[alias] = bool(tools_map)

                # Publish atomically — concurrent callers waiting on the
                # lock will see the completed cache.
                self._connector_names = connector_names
                self._connector_connected = connector_connected
                self._alias_to_id = {alias: cid for cid, alias, _ in unique_connectors}
                self._cache = cache

                return all_tools

    async def _fetch_connector_tools(
        self, client: Mistral, connector: Any, connector_alias: str
    ) -> dict[str, type[BaseTool]] | None:
        """Fetch tools for a single connector via ``list_tools_async``.

        The list endpoint does not always include tools inline, so we
        call ``list_tools_async`` per connector to get the full set.
        """
        connector_id = str(connector.id)
        name = connector.name or connector_id

        try:
            response = await asyncio.wait_for(
                client.beta.connectors.list_tools_async(
                    connector_id_or_name=connector_id
                ),
                timeout=_TOOL_FETCH_TIMEOUT,
            )
        except TimeoutError:
            logger.warning(
                f"Timeout fetching tools for connector {name} (>{_TOOL_FETCH_TIMEOUT}s)"
            )
            return None
        except Exception as exc:
            logger.warning(f"Failed to list tools for connector {name}: {exc}")
            return None

        # The SDK may return a plain list, an iterable wrapper, or an
        # object with a `data`/`items` attribute.  Handle all cases.
        if isinstance(response, list):
            raw_tools: list[Any] = response
        elif hasattr(response, "data"):
            raw_tools = list(response.data or [])
        elif hasattr(response, "items"):
            raw_tools = list(response.items or [])
        else:
            try:
                raw_tools = list(response)
            except TypeError:
                logger.warning(
                    f"Unexpected response type from list_tools_async for {name}: "
                    f"{type(response).__name__}"
                )
                raw_tools = []

        result: dict[str, type[BaseTool]] = {}
        for tool in raw_tools:
            remote = _connector_tool_to_remote(tool)
            if remote is None:
                continue
            try:
                proxy_cls = create_connector_proxy_tool_class(
                    connector_name=name,
                    connector_alias=connector_alias,
                    connector_id=connector_id,
                    remote=remote,
                    api_key=self._api_key,
                    server_url=self._server_url,
                )
                result[proxy_cls.get_name()] = proxy_cls
            except Exception as exc:
                tool_name = (
                    tool.get("name")
                    if isinstance(tool, dict)
                    else getattr(tool, "name", "<unknown>")
                )
                logger.warning(
                    f"Failed to register connector tool {tool_name} for {name}: {exc}"
                )
        return result

    @property
    def connector_count(self) -> int:
        if self._cache is None:
            return 0
        return len(self._connector_names)

    def get_connector_names(self) -> list[str]:
        return list(self._connector_names)

    def is_connected(self, name: str) -> bool:
        return self._connector_connected.get(name, False)

    def get_connector_id(self, alias: str) -> str | None:
        """Return the API connector ID for a given alias, or None."""
        return self._alias_to_id.get(alias)

    async def refresh_connector_async(self, alias: str) -> dict[str, type[BaseTool]]:
        """Re-fetch tools for a single connector by alias.

        Updates the internal cache for that connector only. Returns
        the new tool map (empty dict on failure).
        """
        connector_id = self._alias_to_id.get(alias)
        if connector_id is None:
            return {}

        try:
            async with self._get_client() as client:
                connector = await client.beta.connectors.get_async(
                    connector_id_or_name=connector_id
                )
                tools_map = await self._fetch_connector_tools(client, connector, alias)
        except Exception:
            logger.debug("Failed to refresh connector %s", alias)
            tools_map = None

        if self._cache is None:
            self._cache = {}

        if tools_map is None:
            self._cache.pop(connector_id, None)
            self._connector_connected[alias] = False
            return {}

        self._cache[connector_id] = tools_map
        self._connector_connected[alias] = bool(tools_map)
        return tools_map

    async def get_auth_url(self, alias: str) -> str | None:
        """Return the OAuth authorization URL for a connector, or None.

        Returns None when the connector does not support OAuth or the
        alias is unknown.
        """
        connector_id = self._alias_to_id.get(alias)
        if connector_id is None:
            return None
        try:
            async with self._get_client() as client:
                result = await client.beta.connectors.get_auth_url_async(
                    connector_id_or_name=connector_id
                )
            return result.auth_url
        except Exception:
            logger.debug("Failed to get auth URL for connector %s", alias)
            return None

    def clear(self) -> None:
        self._cache = None
        self._connector_names = []
        self._connector_connected = {}
        self._alias_to_id = {}
