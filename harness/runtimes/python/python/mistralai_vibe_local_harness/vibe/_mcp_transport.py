"""MCP SDK and HTTPX2 boundary for Unified Harness transports."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import importlib.metadata
import logging
import tempfile
from typing import Any, Self, cast

import anyio
import httpx2
from mcp import ClientSession
from mcp.client.auth import OAuthFlowError
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import PaginatedRequestParams
from pydantic import JsonValue, TypeAdapter

from mistralai_vibe_local_harness.vibe._http_transport import env_proxy_transport
from mistralai_vibe_local_harness.vibe._mcp_models import (
    JsonObject,
    JsonSchema,
    MCPAuthorizationRejected,
    MCPAuthorizationSnapshot,
    MCPClientConnection,
    MCPHTTPStatusFailure,
    MCPHTTPTransportPolicy,
    MCPNormalizedResult,
    MCPRemoteToolDescriptor,
    MCPRuntimeFailure,
    MCPSamplingCallback,
    MCPTransportDisconnected,
    MCPTransportFactory,
    ResolvedMCPServerConfig,
)
from mistralai_vibe_local_harness.vibe._ssl import build_ssl_context

logger = logging.getLogger(__name__)
_JSON_VALUE = TypeAdapter(JsonValue)
_JSON_SCHEMA = TypeAdapter(JsonSchema)
_TRANSPORT_FAILURES = (
    anyio.BrokenResourceError,
    anyio.ClosedResourceError,
    BrokenPipeError,
    ConnectionError,
    EOFError,
)
_MAX_TOOLS_LIST_PAGES = 1_000
_MAX_DISCOVERED_TOOLS = 1_000
# User-Agent sent on every MCP HTTP call so downstream services (e.g.
# connectors-gateway) can attribute the call to the Vibe CLI.


def _vibe_distribution_version() -> str:
    try:
        return importlib.metadata.version("mistral-vibe")
    except importlib.metadata.PackageNotFoundError:
        return importlib.metadata.version("mistralai-vibe-local-harness")


_VIBE_CLI_USER_AGENT = f"MistralAI-VibeCLI/{_vibe_distribution_version()}"


class UnifiedMCPTransportFactory(MCPTransportFactory):
    async def open_stdio(
        self,
        server: ResolvedMCPServerConfig,
        *,
        sampling_callback: MCPSamplingCallback | None,
    ) -> MCPClientConnection:
        return await _StdioConnection.open(server, sampling_callback=sampling_callback)


async def discover_http(
    server: ResolvedMCPServerConfig,
    authorization: MCPAuthorizationSnapshot,
    *,
    http_transport_policy: MCPHTTPTransportPolicy,
    sampling_callback: MCPSamplingCallback | None = None,
) -> tuple[MCPRemoteToolDescriptor, ...]:
    if not server.url:
        raise MCPRuntimeFailure(
            "mcp_invalid_configuration", "MCP HTTP server has no URL"
        )
    try:
        async with _http_session(
            server,
            authorization,
            http_transport_policy,
            sampling_callback=sampling_callback,
        ) as session:
            return await _list_all_tools(session)
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        raise _translate_failure(exc, operation="discovery") from exc


async def call_http(
    server: ResolvedMCPServerConfig,
    authorization: MCPAuthorizationSnapshot,
    *,
    http_transport_policy: MCPHTTPTransportPolicy,
    tool_name: str,
    arguments: JsonObject,
    sampling_callback: MCPSamplingCallback | None = None,
) -> MCPNormalizedResult:
    if not server.url:
        raise MCPRuntimeFailure(
            "mcp_invalid_configuration", "MCP HTTP server has no URL"
        )
    try:
        async with _http_session(
            server,
            authorization,
            http_transport_policy,
            sampling_callback=sampling_callback,
        ) as session:
            result = await session.call_tool(
                tool_name,
                cast(dict[str, Any], arguments),
                read_timeout_seconds=server.tool_timeout_s,
            )
            return _normalize_result(result)
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        raise _translate_failure(exc, operation="call") from exc


class _HttpSessionContext:
    def __init__(
        self,
        server: ResolvedMCPServerConfig,
        authorization: MCPAuthorizationSnapshot,
        http_transport_policy: MCPHTTPTransportPolicy,
        sampling_callback: MCPSamplingCallback | None,
    ) -> None:
        self._server = server
        self._authorization = authorization
        self._http_transport_policy = http_transport_policy
        self._sampling_callback = sampling_callback
        self._stack = AsyncExitStack()

    async def __aenter__(self) -> ClientSession:
        server = self._server
        logger.debug("Opening MCP HTTP session for %r (%s)", server.name, server.url)
        try:
            client = await self._stack.enter_async_context(
                await _build_http_client_off_loop(
                    authorized_url=cast(str, server.url),
                    follow_redirects=True,
                    headers=dict(self._authorization.headers),
                    timeout=httpx2.Timeout(
                        server.startup_timeout_s, read=server.tool_timeout_s
                    ),
                    policy=self._http_transport_policy,
                )
            )
            read, write = await self._stack.enter_async_context(
                streamable_http_client(cast(str, server.url), http_client=client)
            )
            session = await self._stack.enter_async_context(
                ClientSession(
                    read,
                    write,
                    read_timeout_seconds=server.startup_timeout_s,
                    sampling_callback=cast(Any, self._sampling_callback),
                )
            )
            await session.initialize()
        except BaseException as exc:
            # __aexit__ only runs once __aenter__ returns, so a failed handshake here
            # would leak the entered
            # streamable-HTTP client. Its async generator is then finalized by the
            # garbage collector on a *different* task, which raises "Attempted to exit
            # cancel scope in a different task than it was entered in" as an unretrieved
            # task exception. Tear the transport down here, on the task that entered it.
            logger.debug(
                "MCP HTTP session entry failed for %r (%s): %s; closing transport in-task",
                server.name,
                server.url,
                type(exc).__name__,
            )
            teardown_error = await self._aclose_after_failed_entry()
            logger.debug(
                "MCP HTTP transport closed after failed entry for %r (%s)",
                server.name,
                server.url,
            )
            # The MCP SDK cancels its internal task-group scope when the handshake
            # fails, so `initialize()` raises a CancelledError while the concrete cause
            # (e.g. a 401) only surfaces when the transport is torn down. Prefer that
            # concrete cause so discovery can translate and isolate the source instead
            # of the bare CancelledError aborting session open. A genuine cancellation
            # (teardown also cancelled) still propagates untouched.
            if teardown_error is not None and not _is_cancellation(teardown_error):
                raise teardown_error from exc
            raise
        logger.debug("MCP HTTP session ready for %r (%s)", server.name, server.url)
        return session

    async def _aclose_after_failed_entry(self) -> BaseException | None:
        try:
            await self._stack.aclose()
        except BaseException as exc:
            return exc
        return None

    async def __aexit__(
        self, exception_type: object, exception: object, traceback: object
    ) -> None:
        logger.debug(
            "Closing MCP HTTP session for %r (%s)", self._server.name, self._server.url
        )
        await self._stack.aclose()


def _http_session(
    server: ResolvedMCPServerConfig,
    authorization: MCPAuthorizationSnapshot,
    http_transport_policy: MCPHTTPTransportPolicy,
    *,
    sampling_callback: MCPSamplingCallback | None,
) -> _HttpSessionContext:
    return _HttpSessionContext(
        server, authorization, http_transport_policy, sampling_callback
    )


class _StdioConnection(MCPClientConnection):
    def __init__(
        self, stack: AsyncExitStack, session: ClientSession, stderr: Any
    ) -> None:
        self._stack = stack
        self._session = session
        self._stderr = stderr
        self._closed = False

    @classmethod
    async def open(
        cls,
        server: ResolvedMCPServerConfig,
        *,
        sampling_callback: MCPSamplingCallback | None,
    ) -> Self:
        if not server.command:
            raise MCPRuntimeFailure(
                "mcp_invalid_configuration", "MCP stdio server has no command"
            )
        stack = AsyncExitStack()
        stderr = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        try:
            read, write = await stack.enter_async_context(
                stdio_client(
                    StdioServerParameters(
                        command=server.command,
                        args=list(server.args),
                        env=dict(server.env) or None,
                        cwd=str(server.cwd) if server.cwd is not None else None,
                    ),
                    errlog=stderr,
                )
            )
            session = await stack.enter_async_context(
                ClientSession(
                    read,
                    write,
                    read_timeout_seconds=server.startup_timeout_s,
                    sampling_callback=cast(Any, sampling_callback),
                )
            )
            await session.initialize()
        except asyncio.CancelledError:
            await stack.aclose()
            stderr.close()
            raise
        except BaseException as exc:
            await stack.aclose()
            _log_stderr(stderr)
            stderr.close()
            raise _translate_failure(exc, operation="stdio startup") from exc
        return cls(stack, session, stderr)

    async def list_tools(self) -> tuple[MCPRemoteToolDescriptor, ...]:
        try:
            return await _list_all_tools(self._session)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            raise _translate_failure(exc, operation="discovery") from exc

    async def call_tool(
        self, name: str, arguments: JsonObject, *, timeout_s: float
    ) -> MCPNormalizedResult:
        try:
            result = await self._session.call_tool(
                name, cast(dict[str, Any], arguments), read_timeout_seconds=timeout_s
            )
            return _normalize_result(result)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            raise _translate_failure(exc, operation="call") from exc

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._stack.aclose()
        finally:
            _log_stderr(self._stderr)
            self._stderr.close()


async def _list_all_tools(
    session: ClientSession,
) -> tuple[MCPRemoteToolDescriptor, ...]:
    descriptors: list[MCPRemoteToolDescriptor] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    while True:
        if len(seen_cursors) >= _MAX_TOOLS_LIST_PAGES:
            raise MCPRuntimeFailure(
                "mcp_pagination_limit", "MCP tools/list returned too many pages"
            )
        page = await session.list_tools(
            params=None if cursor is None else PaginatedRequestParams(cursor=cursor)
        )
        descriptors.extend(_normalize_descriptor(tool) for tool in page.tools)
        if len(descriptors) > _MAX_DISCOVERED_TOOLS:
            raise MCPRuntimeFailure(
                "mcp_descriptor_limit", "MCP server returned too many tools"
            )
        next_cursor = page.next_cursor
        if next_cursor is None:
            return tuple(descriptors)
        if next_cursor in seen_cursors:
            raise MCPRuntimeFailure(
                "mcp_invalid_pagination", "MCP tools/list repeated a cursor"
            )
        seen_cursors.add(next_cursor)
        cursor = next_cursor


def _normalize_descriptor(value: object) -> MCPRemoteToolDescriptor:
    raw = _model_dump(value)
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise MCPRuntimeFailure(
            "mcp_invalid_descriptor", "MCP tool descriptor has no name"
        )
    description = raw.get("description")
    input_schema = _JSON_SCHEMA.validate_python(raw.get("inputSchema", {}))
    output_raw = raw.get("outputSchema")
    output_schema = (
        None if output_raw is None else _JSON_SCHEMA.validate_python(output_raw)
    )
    return MCPRemoteToolDescriptor(
        remote_name=name,
        description=description if isinstance(description, str) else "",
        input_schema=input_schema,
        output_schema=output_schema,
        annotations=_JSON_VALUE.validate_python(raw.get("annotations")),
    )


def _normalize_result(value: object) -> MCPNormalizedResult:
    raw = _model_dump(value)
    raw_content = raw.get("content", [])
    if not isinstance(raw_content, list):
        raise MCPRuntimeFailure(
            "mcp_invalid_result", "MCP tool result content is not a list"
        )
    content: list[JsonObject] = []
    for block in raw_content:
        normalized = _JSON_VALUE.validate_python(block)
        if not isinstance(normalized, dict):
            raise MCPRuntimeFailure(
                "mcp_invalid_result", "MCP tool result block is not an object"
            )
        block_type = normalized.get("type")
        if block_type not in {"text", "image", "audio", "resource_link", "resource"}:
            raise MCPRuntimeFailure(
                "mcp_invalid_result", "MCP tool result has an unsupported content block"
            )
        content.append(_without_unset_fields(normalized))
    structured = _JSON_VALUE.validate_python(raw.get("structuredContent"))
    meta_raw = _JSON_VALUE.validate_python(raw.get("_meta"))
    if meta_raw is not None and not isinstance(meta_raw, dict):
        raise MCPRuntimeFailure("mcp_invalid_result", "MCP result metadata is invalid")
    is_error = raw.get("isError", False)
    if not isinstance(is_error, bool):
        raise MCPRuntimeFailure("mcp_invalid_result", "MCP isError is not a boolean")
    return MCPNormalizedResult(
        content=tuple(content),
        structured_content=structured,
        meta=cast(JsonObject | None, meta_raw),
        is_error=is_error,
    )


def _without_unset_fields(block: dict[str, JsonValue]) -> JsonObject:
    """``block`` without the null fields the SDK dump fills in for unset ones.

    The dump keeps ``None`` fields, so a resource link without icons arrives
    as ``"icons": null`` where the Harness block wants a list, and would fail
    the whole call. Only the block and its SDK-modelled parts (annotations,
    embedded resource, icons) are cleaned: ``_meta`` and the other values are
    the server's own JSON and keep their nulls.
    """
    cleaned: JsonObject = {}
    for key, value in block.items():
        if value is None:
            continue
        if key in {"annotations", "resource"} and isinstance(value, dict):
            value = _without_nulls(value)
        elif key == "icons" and isinstance(value, list):
            value = [
                _without_nulls(icon) if isinstance(icon, dict) else icon
                for icon in value
            ]
        cleaned[key] = value
    return cleaned


def _without_nulls(value: dict[str, JsonValue]) -> JsonObject:
    return {key: inner for key, inner in value.items() if inner is not None}


def _model_dump(value: object) -> dict[str, Any]:
    dump = getattr(value, "model_dump", None)
    if not callable(dump):
        raise MCPRuntimeFailure(
            "mcp_invalid_payload", "MCP SDK returned an invalid value"
        )
    raw = dump(mode="json", by_alias=True, exclude_none=False)
    if not isinstance(raw, dict):
        raise MCPRuntimeFailure(
            "mcp_invalid_payload", "MCP SDK returned an invalid object"
        )
    return cast(dict[str, Any], raw)


def _translate_failure(exc: BaseException, *, operation: str) -> MCPRuntimeFailure:  # noqa: PLR0911 - one return per failure kind
    flattened = _flatten_exceptions(exc)
    for nested in flattened:
        if (
            isinstance(nested, httpx2.HTTPStatusError)
            and nested.response.status_code == httpx2.codes.UNAUTHORIZED
        ):
            return MCPAuthorizationRejected(
                f"MCP {operation} requires authorization", reason="http_unauthorized"
            )
        if isinstance(nested, OAuthFlowError):
            return MCPAuthorizationRejected(
                f"MCP {operation} requires authorization", reason="mcp_unauthorized"
            )
        if isinstance(nested, httpx2.HTTPStatusError):
            return MCPHTTPStatusFailure(
                nested.response.status_code,
                f"MCP {operation} failed with an HTTP error",
            )
        if isinstance(nested, MCPRuntimeFailure):
            return nested
        if isinstance(nested, TimeoutError | httpx2.TimeoutException):
            return MCPRuntimeFailure(
                "mcp_timeout", f"MCP {operation} timed out", retryable=True
            )
        if isinstance(nested, _TRANSPORT_FAILURES):
            return MCPTransportDisconnected(f"MCP {operation} transport disconnected")
    return MCPRuntimeFailure("mcp_transport_failed", f"MCP {operation} failed")


def _flatten_exceptions(exc: BaseException) -> tuple[BaseException, ...]:
    if isinstance(exc, BaseExceptionGroup):
        return tuple(
            nested for child in exc.exceptions for nested in _flatten_exceptions(child)
        )
    return (exc,)


def _is_cancellation(exc: BaseException) -> bool:
    """True when *exc* carries only cancellation, so it must keep propagating.

    A handshake failure surfaces the concrete cause (e.g. a 401) nested in the group,
    which is not cancellation and should be preferred over the SDK's internal scope
    CancelledError.
    """
    return all(
        isinstance(nested, asyncio.CancelledError)
        for nested in _flatten_exceptions(exc)
    )


def _build_http_client(
    *,
    authorized_url: str,
    follow_redirects: bool,
    headers: dict[str, str],
    timeout: httpx2.Timeout,
    policy: MCPHTTPTransportPolicy,
) -> httpx2.AsyncClient:
    if not any(k.lower() == "user-agent" for k in headers):
        headers["User-Agent"] = _VIBE_CLI_USER_AGENT
    verify = build_ssl_context(use_system_trust_store=policy.enable_system_trust_store)
    redirect_guard = _AuthorizationRedirectGuard(authorized_url, tuple(headers))
    return httpx2.AsyncClient(
        event_hooks={"request": [redirect_guard], "response": [_raise_for_status]},
        follow_redirects=follow_redirects,
        headers=headers,
        timeout=timeout,
        trust_env=False,
        transport=env_proxy_transport(verify=verify),
    )


async def _build_http_client_off_loop(
    *,
    authorized_url: str,
    follow_redirects: bool,
    headers: dict[str, str],
    timeout: httpx2.Timeout,
    policy: MCPHTTPTransportPolicy,
) -> httpx2.AsyncClient:
    return await asyncio.to_thread(
        _build_http_client,
        authorized_url=authorized_url,
        follow_redirects=follow_redirects,
        headers=headers,
        timeout=timeout,
        policy=policy,
    )


class _AuthorizationRedirectGuard:
    def __init__(self, authorized_url: str, header_names: tuple[str, ...]) -> None:
        self._authorized_origin = _url_origin(httpx2.URL(authorized_url))
        self._header_names = header_names

    async def __call__(self, request: httpx2.Request) -> None:
        if _url_origin(request.url) == self._authorized_origin:
            return
        for name in self._header_names:
            request.headers.pop(name, None)


async def _raise_for_status(response: httpx2.Response) -> None:
    # mcp 2 answers an HTTP error on a POST with a generic JSON-RPC error;
    # raising keeps the status, which tells a rejected credential from a
    # missing route.
    if response.request.method == "POST" and response.is_error:
        await response.aread()
        response.raise_for_status()


def _url_origin(url: httpx2.URL) -> tuple[str, str, int | None]:
    port = url.port or {"http": 80, "https": 443}.get(url.scheme)
    return url.scheme.lower(), (url.host or "").lower(), port


def _log_stderr(stderr: Any) -> None:
    try:
        stderr.flush()
        stderr.seek(0)
        for line in stderr:
            if str(line).strip():
                logger.debug("MCP stdio server emitted stderr")
                return
    except (OSError, ValueError):
        return


__all__ = ["UnifiedMCPTransportFactory", "call_http", "discover_http"]
