from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import timedelta
import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from pydantic import BaseModel, ConfigDict, Field, field_validator

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay
from vibe.core.types import ToolStreamEvent

if TYPE_CHECKING:
    from vibe.core.types import ToolCallEvent, ToolResultEvent


class _OpenArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class MCPToolResult(BaseModel):
    ok: bool = True
    server: str
    tool: str
    text: str | None = None
    structured: dict[str, Any] | None = None


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


async def list_tools_http(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    startup_timeout_sec: float | None = None,
) -> list[RemoteTool]:
    timeout = timedelta(seconds=startup_timeout_sec) if startup_timeout_sec else None
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write, read_timeout_seconds=timeout) as session:
            await session.initialize()
            tools_resp = await session.list_tools()
            return [RemoteTool.model_validate(t) for t in tools_resp.tools]


async def call_tool_http(
    url: str,
    tool_name: str,
    arguments: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    startup_timeout_sec: float | None = None,
    tool_timeout_sec: float | None = None,
) -> MCPToolResult:
    init_timeout = (
        timedelta(seconds=startup_timeout_sec) if startup_timeout_sec else None
    )
    call_timeout = timedelta(seconds=tool_timeout_sec) if tool_timeout_sec else None
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(
            read, write, read_timeout_seconds=init_timeout
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
    startup_timeout_sec: float | None = None,
    tool_timeout_sec: float | None = None,
) -> type[BaseTool[_OpenArgs, MCPToolResult, BaseToolConfig, BaseToolState]]:
    from urllib.parse import urlparse

    def _alias_from_url(url: str) -> str:
        p = urlparse(url)
        host = (p.hostname or "mcp").replace(".", "_")
        port = f"_{p.port}" if p.port else ""
        return f"{host}{port}"

    published_name = f"{(alias or _alias_from_url(url))}_{remote.name}"

    class MCPHttpProxyTool(
        BaseTool[_OpenArgs, MCPToolResult, BaseToolConfig, BaseToolState]
    ):
        description: ClassVar[str] = (
            (f"[{alias}] " if alias else "")
            + (remote.description or f"MCP tool '{remote.name}' from {url}")
            + (f"\nHint: {server_hint}" if server_hint else "")
        )
        _mcp_url: ClassVar[str] = url
        _remote_name: ClassVar[str] = remote.name
        _input_schema: ClassVar[dict[str, Any]] = remote.input_schema
        _headers: ClassVar[dict[str, str]] = dict(headers or {})
        _startup_timeout_sec: ClassVar[float | None] = startup_timeout_sec
        _tool_timeout_sec: ClassVar[float | None] = tool_timeout_sec

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
                payload = args.model_dump(exclude_none=True)
                yield await call_tool_http(
                    self._mcp_url,
                    self._remote_name,
                    payload,
                    headers=self._headers,
                    startup_timeout_sec=self._startup_timeout_sec,
                    tool_timeout_sec=self._tool_timeout_sec,
                )
            except Exception as exc:
                raise ToolError(f"MCP call failed: {exc}") from exc

        @classmethod
        def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
            return ToolCallDisplay(summary=f"{published_name}")

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

    MCPHttpProxyTool.__name__ = f"MCP_{(alias or _alias_from_url(url))}__{remote.name}"
    return MCPHttpProxyTool


async def list_tools_stdio(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
    startup_timeout_sec: float | None = None,
) -> list[RemoteTool]:
    params = StdioServerParameters(command=command[0], args=command[1:], env=env)
    timeout = timedelta(seconds=startup_timeout_sec) if startup_timeout_sec else None
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=timeout) as session:
            await session.initialize()
            tools_resp = await session.list_tools()
            return [RemoteTool.model_validate(t) for t in tools_resp.tools]


async def call_tool_stdio(
    command: list[str],
    tool_name: str,
    arguments: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
    startup_timeout_sec: float | None = None,
    tool_timeout_sec: float | None = None,
) -> MCPToolResult:
    params = StdioServerParameters(command=command[0], args=command[1:], env=env)
    init_timeout = (
        timedelta(seconds=startup_timeout_sec) if startup_timeout_sec else None
    )
    call_timeout = timedelta(seconds=tool_timeout_sec) if tool_timeout_sec else None
    async with stdio_client(params) as (read, write):
        async with ClientSession(
            read, write, read_timeout_seconds=init_timeout
        ) as session:
            await session.initialize()
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
    startup_timeout_sec: float | None = None,
    tool_timeout_sec: float | None = None,
) -> type[BaseTool[_OpenArgs, MCPToolResult, BaseToolConfig, BaseToolState]]:
    def _alias_from_command(cmd: list[str]) -> str:
        prog = Path(cmd[0]).name.replace(".", "_") if cmd else "mcp"
        digest = hashlib.blake2s(
            "\0".join(cmd).encode("utf-8"), digest_size=4
        ).hexdigest()
        return f"{prog}_{digest}"

    computed_alias = alias or _alias_from_command(command)
    published_name = f"{computed_alias}_{remote.name}"

    class MCPStdioProxyTool(
        BaseTool[_OpenArgs, MCPToolResult, BaseToolConfig, BaseToolState]
    ):
        description: ClassVar[str] = (
            (f"[{computed_alias}] " if computed_alias else "")
            + (
                remote.description
                or f"MCP tool '{remote.name}' from stdio command: {' '.join(command)}"
            )
            + (f"\nHint: {server_hint}" if server_hint else "")
        )
        _stdio_command: ClassVar[list[str]] = command
        _remote_name: ClassVar[str] = remote.name
        _input_schema: ClassVar[dict[str, Any]] = remote.input_schema
        _env: ClassVar[dict[str, str] | None] = env
        _startup_timeout_sec: ClassVar[float | None] = startup_timeout_sec
        _tool_timeout_sec: ClassVar[float | None] = tool_timeout_sec

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
                payload = args.model_dump(exclude_none=True)
                result = await call_tool_stdio(
                    self._stdio_command,
                    self._remote_name,
                    payload,
                    env=self._env,
                    startup_timeout_sec=self._startup_timeout_sec,
                    tool_timeout_sec=self._tool_timeout_sec,
                )
                yield result
            except Exception as exc:
                raise ToolError(f"MCP stdio call failed: {exc!r}") from exc

        @classmethod
        def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
            return ToolCallDisplay(summary=f"{published_name}")

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
