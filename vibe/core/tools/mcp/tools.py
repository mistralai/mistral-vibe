from __future__ import annotations

from collections.abc import AsyncGenerator
import contextlib
import hashlib
import os
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Any, ClassVar, TextIO

import httpx2

from vibe import __version__
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState
from vibe.core.tools.mcp.authorization import (
    MCPAuthorizationRequired,
    MCPAuthorizationSnapshot,
)
from vibe.core.tools.remote import MCPTool, MCPToolResult, RemoteTool, _OpenArgs
from vibe.observability.logging import logger
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context
from vibe.utils.io import decode_console_safe

# The MCP SDK is imported where used: its package import loads ~230 modules.
if TYPE_CHECKING:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters
    from mcp.types import Tool


# Mirrors MCP's default Streamable HTTP timeout values while avoiding an import from
# mcp.shared._httpx_utils, which is an internal module.
_MCP_DEFAULT_TIMEOUT = 30.0
_MCP_DEFAULT_SSE_READ_TIMEOUT = 300.0


def _stderr_logger_thread(read_fd: int) -> None:
    with open(read_fd, "rb") as f:
        for line in iter(f.readline, b""):
            decoded = decode_console_safe(line).rstrip()
            if decoded:
                logger.debug(f"[MCP stderr] {decoded}")


@contextlib.asynccontextmanager
async def _mcp_stderr_capture() -> AsyncGenerator[TextIO]:
    r, w = os.pipe()
    errlog = None
    thread_started = False
    try:
        thread = threading.Thread(target=_stderr_logger_thread, args=(r,), daemon=True)
        thread.start()
        thread_started = True
        errlog = os.fdopen(w, "w")
        yield errlog
    finally:
        if errlog is not None:
            errlog.close()
        elif thread_started:
            os.close(w)
        else:
            os.close(r)
            os.close(w)


def create_vibe_mcp_http_client(
    headers: dict[str, str] | None, *, auth: httpx2.Auth | None = None
) -> VibeAsyncHTTPClient:
    effective_headers = dict(headers or {})
    if not any(k.lower() == "user-agent" for k in effective_headers):
        effective_headers["User-Agent"] = f"MistralAI-VibeCLI/{__version__}"
    return VibeAsyncHTTPClient(
        follow_redirects=True,
        headers=effective_headers,
        auth=_RaiseForStatus(auth),
        timeout=httpx2.Timeout(
            _MCP_DEFAULT_TIMEOUT, read=_MCP_DEFAULT_SSE_READ_TIMEOUT
        ),
        verify=build_ssl_context(),
    )


class _RaiseForStatus(httpx2.Auth):
    """Raise an HTTP error on a POST once ``inner`` is done with the response.

    mcp 2 answers an HTTP error on a POST with a generic JSON-RPC error;
    raising keeps the status, so a 401 still reads as an authorization
    rejection. Raising after ``inner`` lets an OAuth provider answer the 401
    first; the provider's flow is always closed, which releases its lock.
    """

    def __init__(self, inner: httpx2.Auth | None) -> None:
        self._inner = inner

    async def async_auth_flow(
        self, request: httpx2.Request
    ) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        if self._inner is None:
            response = yield request
        else:
            flow = self._inner.async_auth_flow(request)
            try:
                next_request = await anext(flow)
                while True:
                    response = yield next_request
                    try:
                        next_request = await flow.asend(response)
                    except StopAsyncIteration:
                        break
            finally:
                await flow.aclose()
        if response.request.method == "POST" and response.is_error:
            await response.aread()
            response.raise_for_status()


async def list_tools_http(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    auth: httpx2.Auth | None = None,
    startup_timeout_sec: float | None = None,
) -> list[RemoteTool]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    timeout = startup_timeout_sec or None
    async with create_vibe_mcp_http_client(headers, auth=auth) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (
            read,
            write,
        ):
            async with ClientSession(
                read, write, read_timeout_seconds=timeout
            ) as session:
                await session.initialize()
                tools_resp = await session.list_tools()
                return _remote_tools(tools_resp.tools)


def _remote_tools(tools: list[Tool]) -> list[RemoteTool]:
    return [RemoteTool.model_validate(tool.model_dump(by_alias=True)) for tool in tools]


def create_mcp_http_proxy_tool_class(
    *,
    url: str,
    remote: RemoteTool,
    alias: str | None = None,
    server_hint: str | None = None,
) -> type[BaseTool[_OpenArgs, MCPToolResult, BaseToolConfig, BaseToolState]]:
    from urllib.parse import urlparse

    def _alias_from_url(url: str) -> str:
        p = urlparse(url)
        host = (p.hostname or "mcp").replace(".", "_")
        port = f"_{p.port}" if p.port else ""
        return f"{host}{port}"

    computed_alias = alias or _alias_from_url(url)
    published_name = f"{computed_alias}_{remote.name}"

    class MCPHttpProxyTool(MCPTool):
        description: ClassVar[str] = (
            (f"[{computed_alias}] " if computed_alias else "")
            + (remote.description or f"MCP tool '{remote.name}' from {url}")
            + (f"\nHint: {server_hint}" if server_hint else "")
        )
        _server_name: ClassVar[str] = computed_alias
        _remote_name: ClassVar[str] = remote.name
        _input_schema: ClassVar[dict[str, Any]] = remote.input_schema

        @classmethod
        def get_name(cls) -> str:
            return published_name

        @classmethod
        def get_parameters(cls) -> dict[str, Any]:
            return dict(cls._input_schema)

    MCPHttpProxyTool.__name__ = f"MCP_{computed_alias}__{remote.name}"
    return MCPHttpProxyTool


def authorization_required_result(
    result: MCPAuthorizationSnapshot | MCPAuthorizationRequired,
    *,
    observed_connection_revision: str,
) -> MCPAuthorizationRequired:
    if isinstance(result, MCPAuthorizationRequired):
        return result
    return MCPAuthorizationRequired(
        reason="rejected",
        descriptor_revision=result.descriptor_revision,
        observed_connection_revision=observed_connection_revision,
    )


def is_authorization_rejection(exc: BaseException) -> bool:
    if isinstance(exc, BaseExceptionGroup):
        return any(is_authorization_rejection(child) for child in exc.exceptions)
    if isinstance(exc, httpx2.HTTPStatusError):
        return exc.response.status_code == httpx2.codes.UNAUTHORIZED
    from mcp.client.auth import OAuthFlowError

    return isinstance(exc, OAuthFlowError)


def build_stdio_params(
    command: list[str], *, env: dict[str, str] | None = None, cwd: str | None = None
) -> StdioServerParameters:
    from mcp.client.stdio import StdioServerParameters

    return StdioServerParameters(command=command[0], args=command[1:], env=env, cwd=cwd)


async def enter_stdio_session(
    stack: contextlib.AsyncExitStack,
    params: StdioServerParameters,
    *,
    init_timeout: float | None,
) -> ClientSession:
    """Enter the stderr-capture, stdio_client, and ClientSession contexts on *stack*.

    The caller owns ``stack`` and decides when to close it. Returns an initialized
    session.
    """
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    errlog = await stack.enter_async_context(_mcp_stderr_capture())
    read, write = await stack.enter_async_context(stdio_client(params, errlog=errlog))
    session = await stack.enter_async_context(
        ClientSession(read, write, read_timeout_seconds=init_timeout)
    )
    await session.initialize()
    return session


async def list_tools_stdio(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    startup_timeout_sec: float | None = None,
) -> list[RemoteTool]:
    params = build_stdio_params(command, env=env, cwd=cwd)
    timeout = startup_timeout_sec or None
    async with contextlib.AsyncExitStack() as stack:
        session = await enter_stdio_session(stack, params, init_timeout=timeout)
        tools_resp = await session.list_tools()
        return _remote_tools(tools_resp.tools)


def create_mcp_stdio_proxy_tool_class(
    *,
    command: list[str],
    remote: RemoteTool,
    alias: str | None = None,
    server_hint: str | None = None,
) -> type[BaseTool[_OpenArgs, MCPToolResult, BaseToolConfig, BaseToolState]]:
    def _alias_from_command(cmd: list[str]) -> str:
        prog = Path(cmd[0]).name.replace(".", "_") if cmd else "mcp"
        digest = hashlib.blake2s(
            "\0".join(cmd).encode("utf-8"), digest_size=4
        ).hexdigest()
        return f"{prog}_{digest}"

    computed_alias = alias or _alias_from_command(command)
    published_name = f"{computed_alias}_{remote.name}"

    class MCPStdioProxyTool(MCPTool):
        description: ClassVar[str] = (
            (f"[{computed_alias}] " if computed_alias else "")
            + (
                remote.description
                or f"MCP tool '{remote.name}' from stdio command: {' '.join(command)}"
            )
            + (f"\nHint: {server_hint}" if server_hint else "")
        )
        _server_name: ClassVar[str] = computed_alias
        _remote_name: ClassVar[str] = remote.name
        _input_schema: ClassVar[dict[str, Any]] = remote.input_schema

        @classmethod
        def get_name(cls) -> str:
            return published_name

        @classmethod
        def get_parameters(cls) -> dict[str, Any]:
            return dict(cls._input_schema)

    MCPStdioProxyTool.__name__ = f"MCP_STDIO_{computed_alias}__{remote.name}"
    return MCPStdioProxyTool
