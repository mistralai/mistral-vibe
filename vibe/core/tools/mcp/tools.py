from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
import contextlib
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import os
from pathlib import Path
import threading
from typing import TYPE_CHECKING, Any, ClassVar, TextIO

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from mcp import ClientSession
from mcp.client.auth import OAuthFlowError
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from vibe.core.logger import logger
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
)
from vibe.core.tools.mcp_sampling import MCPSamplingHandler
from vibe.core.tools.ui import ToolResultDisplay, ToolUIData
from vibe.core.types import ToolStreamEvent
from vibe.core.utils.http import build_ssl_context
from vibe.core.utils.io import decode_safe

if TYPE_CHECKING:
    from vibe.core.types import ToolResultEvent


# Mirrors MCP's default Streamable HTTP timeout values while avoiding an import from
# mcp.shared._httpx_utils, which is an internal module.
_MCP_DEFAULT_TIMEOUT = 30.0
_MCP_DEFAULT_SSE_READ_TIMEOUT = 300.0


def _stderr_logger_thread(read_fd: int) -> None:
    with open(read_fd, "rb") as f:
        for line in iter(f.readline, b""):
            decoded = decode_safe(line, from_subprocess=True).text.rstrip()
            if decoded:
                logger.debug(f"[MCP stderr] {decoded}")


@contextlib.asynccontextmanager
async def _mcp_stderr_capture() -> AsyncGenerator[TextIO, None]:
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


class _OpenArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class MCPToolResult(BaseModel):
    ok: bool = True
    server: str
    tool: str
    text: str | None = None
    structured: dict[str, Any] | None = None


class MCPTool(
    BaseTool[_OpenArgs, MCPToolResult, BaseToolConfig, BaseToolState],
    ToolUIData[_OpenArgs, MCPToolResult],
):
    _server_name: ClassVar[str] = ""
    _remote_name: ClassVar[str] = ""
    _is_connector: ClassVar[bool] = False

    @classmethod
    def get_server_name(cls) -> str | None:
        return cls._server_name or None

    @classmethod
    def get_remote_name(cls) -> str:
        return cls._remote_name or cls.get_name()

    @classmethod
    def is_connector(cls) -> bool:
        return cls._is_connector


class RemoteTool(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str
    description: str | None = None
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
        validation_alias="inputSchema",
    )

    @field_validator("name")
    @classmethod
    def _non_empty_name(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("MCP tool missing valid 'name'")
        return v

    @field_validator("input_schema", mode="before")
    @classmethod
    def _normalize_schema(cls, v: Any) -> dict[str, Any]:
        if v is None:
            return {"type": "object", "properties": {}}
        if isinstance(v, dict):
            return v
        dump = getattr(v, "model_dump", None)
        if callable(dump):
            try:
                v = dump()
            except Exception:
                raise ValueError(
                    "inputSchema must be a dict or have a valid model_dump method"
                )
        if not isinstance(v, dict):
            raise ValueError("inputSchema must be a dict")
        return v


@dataclass(frozen=True)
class MCPHttpOAuthRuntime:
    lock: asyncio.Lock
    failure_callback: Callable[[str], Awaitable[None]]


class _MCPContentBlock(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    text: str | None = None


class _MCPResultIn(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    structuredContent: dict[str, Any] | None = None
    content: list[_MCPContentBlock] | None = None

    @field_validator("structuredContent", mode="before")
    @classmethod
    def _normalize_structured(cls, v: Any) -> dict[str, Any] | None:
        if v is None:
            return None
        if isinstance(v, dict):
            return v
        dump = getattr(v, "model_dump", None)
        if callable(dump):
            try:
                v = dump()
            except Exception:
                return None
        return v if isinstance(v, dict) else None


def _parse_call_result(server: str, tool: str, result_obj: Any) -> MCPToolResult:
    parsed = _MCPResultIn.model_validate(result_obj)
    if (structured := parsed.structuredContent) is not None:
        return MCPToolResult(server=server, tool=tool, text=None, structured=structured)

    blocks = parsed.content or []
    parts = [b.text for b in blocks if isinstance(b.text, str)]
    text = "\n".join(parts) if parts else None
    return MCPToolResult(server=server, tool=tool, text=text, structured=None)


def create_vibe_mcp_http_client(
    headers: dict[str, str] | None, *, auth: httpx.Auth | None = None
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        follow_redirects=True,
        headers=headers,
        auth=auth,
        timeout=httpx.Timeout(_MCP_DEFAULT_TIMEOUT, read=_MCP_DEFAULT_SSE_READ_TIMEOUT),
        verify=build_ssl_context(),
    )


async def list_tools_http(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    auth: httpx.Auth | None = None,
    startup_timeout_sec: float | None = None,
) -> list[RemoteTool]:
    timeout = timedelta(seconds=startup_timeout_sec) if startup_timeout_sec else None
    async with create_vibe_mcp_http_client(headers, auth=auth) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (
            read,
            write,
            _,
        ):
            async with ClientSession(
                read, write, read_timeout_seconds=timeout
            ) as session:
                await session.initialize()
                tools_resp = await session.list_tools()
                return [RemoteTool.model_validate(t) for t in tools_resp.tools]


async def call_tool_http(
    url: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    auth: httpx.Auth | None = None,
    startup_timeout_sec: float | None = None,
    tool_timeout_sec: float | None = None,
    sampling_callback: MCPSamplingHandler | None = None,
) -> MCPToolResult:
    init_timeout = (
        timedelta(seconds=startup_timeout_sec) if startup_timeout_sec else None
    )
    call_timeout = timedelta(seconds=tool_timeout_sec) if tool_timeout_sec else None
    async with create_vibe_mcp_http_client(headers, auth=auth) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (
            read,
            write,
            _,
        ):
            async with ClientSession(
                read,
                write,
                read_timeout_seconds=init_timeout,
                sampling_callback=sampling_callback,
            ) as session:
                await session.initialize()
                result = await session.call_tool(
                    tool_name, arguments, read_timeout_seconds=call_timeout
                )
                return _parse_call_result(url, tool_name, result)


def create_mcp_http_proxy_tool_class(
    *,
    url: str,
    remote: RemoteTool,
    alias: str | None = None,
    server_hint: str | None = None,
    headers: dict[str, str] | None = None,
    auth: httpx.Auth | None = None,
    oauth_runtime: MCPHttpOAuthRuntime | None = None,
    startup_timeout_sec: float | None = None,
    tool_timeout_sec: float | None = None,
    sampling_enabled: bool = True,
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
        _mcp_url: ClassVar[str] = url
        _remote_name: ClassVar[str] = remote.name
        _input_schema: ClassVar[dict[str, Any]] = remote.input_schema
        _headers: ClassVar[dict[str, str]] = dict(headers or {})
        _auth: ClassVar[httpx.Auth | None] = auth
        _oauth_runtime: ClassVar[MCPHttpOAuthRuntime | None] = oauth_runtime
        _startup_timeout_sec: ClassVar[float | None] = startup_timeout_sec
        _tool_timeout_sec: ClassVar[float | None] = tool_timeout_sec
        _sampling_enabled: ClassVar[bool] = sampling_enabled

        @classmethod
        def get_name(cls) -> str:
            return published_name

        @classmethod
        def get_parameters(cls) -> dict[str, Any]:
            return dict(cls._input_schema)

        async def run(
            self, args: _OpenArgs, ctx: InvokeContext | None = None
        ) -> AsyncGenerator[ToolStreamEvent | MCPToolResult, None]:
            try:
                sampling_callback = (
                    ctx.sampling_callback if ctx and self._sampling_enabled else None
                )
                payload = args.model_dump(exclude_none=True)
                if self._oauth_runtime is None:
                    yield await self._call_remote(payload, sampling_callback)
                    return
                async with self._oauth_runtime.lock:
                    result = await self._call_remote(payload, sampling_callback)
                yield result
            except OAuthFlowError as exc:
                if self._oauth_runtime is not None:
                    await self._oauth_runtime.failure_callback(self._server_name)
                raise ToolError(
                    f"MCP server '{self._server_name}' lost authentication. "
                    "Stop the current turn and ask the user to run "
                    f"`/mcp login {self._server_name}` to re-authenticate."
                ) from exc
            except Exception as exc:
                raise ToolError(f"MCP call failed: {exc}") from exc

        @classmethod
        async def _call_remote(
            cls, payload: dict[str, Any], sampling_callback: MCPSamplingHandler | None
        ) -> MCPToolResult:
            return await call_tool_http(
                cls._mcp_url,
                cls._remote_name,
                payload,
                headers=cls._headers,
                auth=cls._auth,
                startup_timeout_sec=cls._startup_timeout_sec,
                tool_timeout_sec=cls._tool_timeout_sec,
                sampling_callback=sampling_callback,
            )

        @classmethod
        def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
            if not isinstance(event.result, MCPToolResult):
                return ToolResultDisplay(
                    success=False,
                    message=event.error or event.skip_reason or "No result",
                )

            message = f"MCP tool {event.result.tool} completed"
            return ToolResultDisplay(success=event.result.ok, message=message)

        @classmethod
        def get_status_text(cls) -> str:
            return f"Calling MCP tool {remote.name}"

    MCPHttpProxyTool.__name__ = f"MCP_{computed_alias}__{remote.name}"
    return MCPHttpProxyTool


def build_stdio_params(
    command: list[str], *, env: dict[str, str] | None = None, cwd: str | None = None
) -> StdioServerParameters:
    return StdioServerParameters(command=command[0], args=command[1:], env=env, cwd=cwd)


async def enter_stdio_session(
    stack: contextlib.AsyncExitStack,
    params: StdioServerParameters,
    *,
    init_timeout: timedelta | None,
    sampling_callback: MCPSamplingHandler | None = None,
) -> ClientSession:
    """Enter the stderr-capture, stdio_client, and ClientSession contexts on *stack*.

    The caller owns ``stack`` and decides when to close it. Returns an initialized
    session. The one-shot helpers close the stack immediately; the connection pool
    keeps it open for the session lifetime.
    """
    errlog = await stack.enter_async_context(_mcp_stderr_capture())
    read, write = await stack.enter_async_context(stdio_client(params, errlog=errlog))
    session = await stack.enter_async_context(
        ClientSession(
            read,
            write,
            read_timeout_seconds=init_timeout,
            sampling_callback=sampling_callback,
        )
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
    timeout = timedelta(seconds=startup_timeout_sec) if startup_timeout_sec else None
    async with contextlib.AsyncExitStack() as stack:
        session = await enter_stdio_session(stack, params, init_timeout=timeout)
        tools_resp = await session.list_tools()
        return [RemoteTool.model_validate(t) for t in tools_resp.tools]


async def call_tool_stdio(
    command: list[str],
    tool_name: str,
    arguments: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    startup_timeout_sec: float | None = None,
    tool_timeout_sec: float | None = None,
    sampling_callback: MCPSamplingHandler | None = None,
) -> MCPToolResult:
    params = build_stdio_params(command, env=env, cwd=cwd)
    init_timeout = (
        timedelta(seconds=startup_timeout_sec) if startup_timeout_sec else None
    )
    call_timeout = timedelta(seconds=tool_timeout_sec) if tool_timeout_sec else None
    async with contextlib.AsyncExitStack() as stack:
        session = await enter_stdio_session(
            stack,
            params,
            init_timeout=init_timeout,
            sampling_callback=sampling_callback,
        )
        result = await session.call_tool(
            tool_name, arguments, read_timeout_seconds=call_timeout
        )
        return _parse_call_result("stdio:" + " ".join(command), tool_name, result)


def create_mcp_stdio_proxy_tool_class(
    *,
    command: list[str],
    remote: RemoteTool,
    alias: str | None = None,
    server_hint: str | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    startup_timeout_sec: float | None = None,
    tool_timeout_sec: float | None = None,
    sampling_enabled: bool = True,
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
        _stdio_command: ClassVar[list[str]] = command
        _remote_name: ClassVar[str] = remote.name
        _input_schema: ClassVar[dict[str, Any]] = remote.input_schema
        _env: ClassVar[dict[str, str] | None] = env
        _cwd: ClassVar[str | None] = cwd
        _startup_timeout_sec: ClassVar[float | None] = startup_timeout_sec
        _tool_timeout_sec: ClassVar[float | None] = tool_timeout_sec
        _sampling_enabled: ClassVar[bool] = sampling_enabled

        @classmethod
        def get_name(cls) -> str:
            return published_name

        @classmethod
        def get_parameters(cls) -> dict[str, Any]:
            return dict(cls._input_schema)

        async def run(
            self, args: _OpenArgs, ctx: InvokeContext | None = None
        ) -> AsyncGenerator[ToolStreamEvent | MCPToolResult, None]:
            try:
                sampling_callback = (
                    ctx.sampling_callback if ctx and self._sampling_enabled else None
                )
                payload = args.model_dump(exclude_none=True)
                pool = ctx.mcp_pool if ctx else None
                if pool is not None:
                    yield await pool.call_tool(
                        command=self._stdio_command,
                        tool_name=self._remote_name,
                        arguments=payload,
                        env=self._env,
                        cwd=self._cwd,
                        startup_timeout_sec=self._startup_timeout_sec,
                        tool_timeout_sec=self._tool_timeout_sec,
                        sampling_callback=sampling_callback,
                    )
                    return
                yield await call_tool_stdio(
                    self._stdio_command,
                    self._remote_name,
                    payload,
                    env=self._env,
                    cwd=self._cwd,
                    startup_timeout_sec=self._startup_timeout_sec,
                    tool_timeout_sec=self._tool_timeout_sec,
                    sampling_callback=sampling_callback,
                )
            except Exception as exc:
                raise ToolError(f"MCP stdio call failed: {exc!r}") from exc

        @classmethod
        def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
            if not isinstance(event.result, MCPToolResult):
                return ToolResultDisplay(
                    success=False,
                    message=event.error or event.skip_reason or "No result",
                )

            message = f"MCP tool {event.result.tool} completed"
            return ToolResultDisplay(success=event.result.ok, message=message)

        @classmethod
        def get_status_text(cls) -> str:
            return f"Calling MCP tool {remote.name}"

    MCPStdioProxyTool.__name__ = f"MCP_STDIO_{computed_alias}__{remote.name}"
    return MCPStdioProxyTool
