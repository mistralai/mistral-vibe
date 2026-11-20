"""Vibebot: the user's local MCP tools, called by the bot through this machine.

The bot's sessions call ``bot.list_local_tools`` and ``bot.call_local_tool``,
client tools that this machine answers like the local-session ones
(``_vibe_bot.py``). Nothing is exposed by default: the user's own config lists
the servers and tools the bot may see (``vibe_bot_local_tools``), and a call
runs without a human only when both its entry and this machine's Vibebot
ceiling (``vibe_bot_max_approval_policy``) allow it. The servers' commands,
environment, URLs and credentials never leave this machine.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, Protocol

from pydantic import Field

from vibe.app_server._model import ProtocolModel
from vibe.core.agents.models import ApprovalPolicyName
from vibe.core.config.models import VibeBotLocalToolsEntry
from vibe.core.utils.matching import name_matches
from vibe.observability.logging import logger

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.vibe._mcp_runtime import MCPRuntime
    from vibe.app_server._session_backend_port import (
        MCPAuthorizationProvider,
        ResolvedMCPCatalog,
        ResolvedMCPServerConfig,
    )
    from vibe.core.config import VibeConfigSchema

LIST_TOOL: Final = "bot.list_local_tools"
CALL_TOOL: Final = "bot.call_local_tool"
LOCAL_TOOLS: Final = frozenset({LIST_TOOL, CALL_TOOL})
VIBE_BOT_LOCAL_TOOLS_METHOD: Final = "vibebot/localTools"
MAX_CALL_SECONDS: Final = 60.0
MAX_LIST_BYTES: Final = 64 * 1024
VIBE_BOT_MCP_CACHE_DIR: Final = "vibe_bot_mcp_descriptors"

type ApprovalMode = Literal["allow", "ask"]


def local_tool_mode(
    entry_approval: ApprovalMode, ceiling: str, *, read_only: bool
) -> ApprovalMode:
    """The strictest of the user's entry and this machine's Vibebot ceiling.

    ``accept-edits`` auto-approves file edits only, so it asks for every MCP
    call; ``smart-approve`` lets tools run that are read-only as the server
    declares it (``readOnlyHint``, checked again on the live route); an unknown
    ceiling asks.
    """
    if entry_approval == "ask":
        return "ask"
    if ceiling == ApprovalPolicyName.AUTO_APPROVE:
        return "allow"
    if ceiling == ApprovalPolicyName.SMART_APPROVE and read_only:
        return "allow"
    return "ask"


@dataclass(frozen=True, slots=True)
class LocalToolDescriptor:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    read_only: bool


@dataclass(frozen=True, slots=True)
class LocalToolResult:
    text: str
    is_error: bool = False


class LocalToolFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class LocalMCP(Protocol):
    async def list_tools(
        self, servers: Sequence[str], *, refresh: bool
    ) -> dict[str, tuple[LocalToolDescriptor, ...]]:
        """Each named server's tools; a server that cannot be reached is left out."""
        ...

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        timeout_s: float,
        require_read_only: bool = False,
    ) -> LocalToolResult:
        """`require_read_only`: the call's `allow` rests on the tool being
        read-only, checked on the live route it runs (not a cached listing).
        """
        ...

    async def aclose(self) -> None: ...


class VibeBotLocalToolsParams(ProtocolModel):
    bot_workflow_id: str = Field(min_length=1)


class VibeBotLocalToolServer(ProtocolModel):
    name: str


class VibeBotLocalToolsResponse(ProtocolModel):
    # What the bot's `<clients>` lists for this machine: the exposed servers'
    # names only, and a digest that changes with what is exposed.
    servers: list[VibeBotLocalToolServer]
    digest: str


class _ListInput(ProtocolModel):
    server: str | None = None
    machine: str | None = None


class LocalTools:
    """What the bot may see and call on this machine, decided from the user's
    own config, read again on every call.
    """

    def __init__(
        self,
        *,
        read_config: Callable[[], Awaitable[VibeConfigSchema]],
        mcp: Callable[[], Awaitable[LocalMCP]],
    ) -> None:
        self._read_config = read_config
        self._mcp_factory = mcp
        self._mcp: LocalMCP | None = None
        self._mcp_lock = asyncio.Lock()
        self._closed = False

    async def summary(self) -> VibeBotLocalToolsResponse:
        config = await self._read_config()
        entries = _exposed_entries(config)
        payload = {
            "ceiling": config.vibe_bot_max_approval_policy,
            "entries": [entry.model_dump() for entry in entries],
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True).encode()
        ).hexdigest()[:16]
        names = sorted({entry.server for entry in entries})
        return VibeBotLocalToolsResponse(
            servers=[VibeBotLocalToolServer(name=name) for name in names], digest=digest
        )

    async def exposes(self, server: str) -> bool:
        return any(
            e.server == server for e in _exposed_entries(await self._read_config())
        )

    async def list(self, raw_input: dict[str, Any], machine: str) -> dict[str, Any]:
        tool_input = _validated(_ListInput, raw_input)
        config = await self._read_config()
        entries = _exposed_entries(config)
        if tool_input.server is not None:
            entries = [e for e in entries if e.server == tool_input.server]
        servers = sorted({e.server for e in entries})
        listed = await (await self._runtime()).list_tools(servers, refresh=True)
        tools: list[dict[str, Any]] = []
        for server in servers:
            for descriptor in listed.get(server, ()):
                entry = _entry_for(entries, server, descriptor.name)
                if entry is None:
                    continue
                tools.append({
                    "machine": machine,
                    "server": server,
                    "tool": descriptor.name,
                    "description": descriptor.description,
                    "inputSchema": dict(descriptor.input_schema),
                    "readOnly": descriptor.read_only,
                    "approval": local_tool_mode(
                        entry.approval,
                        config.vibe_bot_max_approval_policy,
                        read_only=descriptor.read_only,
                    ),
                })
        unreachable = [server for server in servers if server not in listed]
        kept, truncated = _fit(tools, MAX_LIST_BYTES)
        return {
            "tools": kept,
            "unreachableServers": unreachable,
            "truncated": truncated,
        }

    async def mode(self, server: str, tool: str) -> tuple[ApprovalMode, int, float]:
        """How a call to an exposed tool runs: its approval, the byte cap of
        its answer and its timeout. Raises ``not_exposed`` or ``unknown_tool``.
        """
        config = await self._read_config()
        entries = _exposed_entries(config)
        entry = _entry_for(entries, server, tool)
        if entry is None:
            raise LocalToolFailure(
                "not_exposed",
                f"`{server}.{tool}` is not exposed to Vibebot on this computer: "
                "the user lists the tools it may call in vibe_bot_local_tools",
            )
        listed = await (await self._runtime()).list_tools([server], refresh=False)
        if server not in listed:
            raise LocalToolFailure(
                "local_server_unavailable",
                f"`{server}` is configured on this computer but could not be "
                "reached (stopped, failing to start, or waiting for a sign-in)",
            )
        descriptor = next((d for d in listed[server] if d.name == tool), None)
        if descriptor is None:
            raise LocalToolFailure(
                "unknown_tool", f"`{server}` has no tool `{tool}` on this computer"
            )
        server_timeout = next(
            (s.tool_timeout_sec for s in config.mcp_servers if s.name == server),
            MAX_CALL_SECONDS,
        )
        approval = local_tool_mode(
            entry.approval,
            config.vibe_bot_max_approval_policy,
            read_only=descriptor.read_only,
        )
        return (
            approval,
            config.vibe_bot_local_tool_max_bytes,
            min(MAX_CALL_SECONDS, server_timeout),
        )

    async def allowed_only_as_read_only(self, server: str, tool: str) -> bool:
        """Whether an `allow` rests on the tool's read-only claim alone (the
        ceiling is `smart-approve`): the run then checks it on the live route.
        """
        config = await self._read_config()
        entry = _entry_for(_exposed_entries(config), server, tool)
        if entry is None:
            return False
        return (
            local_tool_mode(
                entry.approval, config.vibe_bot_max_approval_policy, read_only=False
            )
            == "ask"
        )

    async def run(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        max_bytes: int,
        timeout_s: float,
        require_read_only: bool = False,
    ) -> dict[str, Any]:
        # The deadline covers the run only: waiting for the server to open
        # is not a call that may have run.
        result = await (await self._runtime()).call_tool(
            server,
            tool,
            arguments,
            timeout_s=timeout_s,
            require_read_only=require_read_only,
        )
        text, truncated, total = _cut(result.text, max_bytes)
        return {
            "content": text,
            "isError": result.is_error,
            "truncated": truncated,
            "totalBytes": total,
        }

    async def aclose(self) -> None:
        async with self._mcp_lock:
            self._closed = True
            mcp, self._mcp = self._mcp, None
        if mcp is not None:
            await mcp.aclose()

    async def drop_runtime(self) -> None:
        """Closes the servers on a detach; the next call opens them again."""
        async with self._mcp_lock:
            mcp, self._mcp = self._mcp, None
        if mcp is not None:
            await mcp.aclose()

    async def _runtime(self) -> LocalMCP:
        async with self._mcp_lock:
            # A call still running at teardown must not open servers nobody closes.
            if self._closed:
                raise LocalToolFailure(
                    "local_tools_unavailable",
                    "Vibebot's local tools are shutting down on this computer",
                )
            if self._mcp is None:
                self._mcp = await self._mcp_factory()
            return self._mcp


def _exposed_entries(config: VibeConfigSchema) -> list[VibeBotLocalToolsEntry]:
    if not config.enable_mcp:
        return []
    configured = {s.name for s in config.mcp_servers if not s.disabled}
    return [e for e in config.vibe_bot_local_tools if e.server in configured]


def _entry_for(
    entries: Sequence[VibeBotLocalToolsEntry], server: str, tool: str
) -> VibeBotLocalToolsEntry | None:
    """The strictest entry that lists the tool: one that asks wins."""
    matching = [
        e for e in entries if e.server == server and name_matches(tool, e.tools)
    ]
    if not matching:
        return None
    return min(matching, key=lambda e: e.approval != "ask")


def _fit(
    tools: list[dict[str, Any]], max_bytes: int
) -> tuple[list[dict[str, Any]], bool]:
    """The tools that fit the answer's cap, in order; the rest are left for a
    listing by `server`.
    """
    kept: list[dict[str, Any]] = []
    size = len(b'{"tools":[],"unreachableServers":[],"truncated":true}') + 1024
    for tool in tools:
        size += len(json.dumps(tool).encode()) + 1
        if size > max_bytes:
            return kept, True
        kept.append(tool)
    return kept, False


def _cut(text: str, max_bytes: int) -> tuple[str, bool, int]:
    data = text.encode()
    if len(data) <= max_bytes:
        return text, False, len(data)
    return data[:max_bytes].decode(errors="ignore"), True, len(data)


def _validated[ModelT: ProtocolModel](
    model: type[ModelT], raw: dict[str, Any]
) -> ModelT:
    from pydantic import ValidationError

    from vibe.app_server._model import validate_wire

    try:
        return validate_wire(model, raw)
    except ValidationError as exc:
        raise LocalToolFailure(
            "invalid_input", f"Invalid input: {exc.errors()[0]['msg']}"
        ) from exc


def _result_text(content: Sequence[Mapping[str, Any]], structured: Any) -> str:
    parts: list[str] = []
    for block in content:
        if block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        else:
            parts.append(f"[{block.get('type', 'content')} omitted]")
    if not parts and structured is not None:
        parts.append(json.dumps(structured))
    return "\n".join(parts)


class HarnessLocalMCP(LocalMCP):
    """The user's servers through the Harness's MCP runtime: the same
    transports, OAuth and descriptor cache as a session's.
    """

    def __init__(
        self,
        *,
        catalog: Callable[[object], Awaitable[ResolvedMCPCatalog]],
        authorization: MCPAuthorizationProvider,
        cache_root: Path,
    ) -> None:
        self._catalog = catalog
        self._authorization = authorization
        self._cache_root = cache_root
        self._runtime: MCPRuntime | None = None
        self._opened: set[str] = set()
        self._lock = asyncio.Lock()

    async def list_tools(
        self, servers: Sequence[str], *, refresh: bool
    ) -> dict[str, tuple[LocalToolDescriptor, ...]]:
        if not servers:
            return {}
        runtime, catalog = await self._configured(servers, refresh=refresh)
        names = {server.name for server in catalog}
        listed: dict[str, tuple[LocalToolDescriptor, ...]] = {}
        for source in runtime.snapshot.sources:
            if source.name not in names or source.status in {
                "needs_auth",
                "unavailable",
            }:
                continue
            # From the routes: they leave out the tools the user turned off
            # and those a session could not call either.
            listed[source.name] = tuple(
                LocalToolDescriptor(
                    name=route.descriptor.remote_name,
                    description=route.descriptor.description,
                    input_schema=route.descriptor.input_schema
                    if isinstance(route.descriptor.input_schema, Mapping)
                    else {},
                    read_only=_read_only(route.descriptor.annotations),
                )
                for route in sorted(
                    runtime.snapshot.routes.values(),
                    key=lambda route: route.descriptor.remote_name,
                )
                if route.descriptor.server_name == source.name
            )
        return listed

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        timeout_s: float,
        require_read_only: bool = False,
    ) -> LocalToolResult:
        from mistralai_vibe_local_harness.vibe._mcp_models import MCPRuntimeFailure

        runtime, _ = await self._configured([server], refresh=False)
        route = next(
            (
                r
                for r in runtime.snapshot.routes.values()
                if r.descriptor.server_name == server
                and r.descriptor.remote_name == tool
            ),
            None,
        )
        if route is None:
            raise LocalToolFailure(
                "unknown_tool", f"`{server}` has no tool `{tool}` on this computer"
            )
        if require_read_only and not _read_only(route.descriptor.annotations):
            raise LocalToolFailure(
                "approval_required",
                f"`{server}.{tool}` no longer says it is read-only: it needs the "
                "user's approval",
            )
        try:
            result = await asyncio.wait_for(
                runtime.execute(
                    group_name=route.group_name,
                    tool_name=route.tool_name,
                    arguments=arguments,
                ),
                timeout_s,
            )
        except TimeoutError as exc:
            raise LocalToolFailure(
                "local_tool_timeout",
                f"`{server}.{tool}` did not answer within {timeout_s:g} s",
            ) from exc
        except MCPRuntimeFailure as exc:
            # The runtime's text can name a command, a path or a URL: it stays here.
            logger.warning(
                "A Vibebot local tool call failed",
                extra={"server": server, "tool": tool, "code": exc.code},
                exc_info=exc,
            )
            raise LocalToolFailure(
                "local_tool_failed",
                f"`{server}.{tool}` failed on this computer ({exc.code})",
            ) from exc
        return LocalToolResult(
            text=_result_text(result.content, result.structured_content),
            is_error=result.is_error,
        )

    async def aclose(self) -> None:
        runtime, self._runtime = self._runtime, None
        if runtime is not None:
            await runtime.aclose()

    async def _configured(
        self, servers: Sequence[str], *, refresh: bool
    ) -> tuple[MCPRuntime, tuple[ResolvedMCPServerConfig, ...]]:
        from vibe.app_server._session_backend_port import ResolvedMCPCatalog

        # Bound under this object: the authentication service keeps a
        # catalog's servers only while its owner lives.
        catalog = await self._catalog(self)
        async with self._lock:
            # A reconfigure closes the servers it leaves out, and other calls
            # may be running on them: keep every server opened so far.
            self._opened.update(servers)
            # Each server's bound comes from its own config only: a call to
            # another server must not retune one that is in use.
            kept = tuple(
                replace(
                    server, tool_timeout_s=min(server.tool_timeout_s, MAX_CALL_SECONDS)
                )
                for server in catalog.servers
                if server.name in self._opened and not server.disabled
            )
            runtime = self._runtime or self._new_runtime()
            self._runtime = runtime
            await runtime.reconfigure(
                _harness_catalog(
                    ResolvedMCPCatalog(revision=catalog.revision, servers=kept)
                ),
                force_remote_discovery=refresh,
                push_to_core=False,
            )
        return runtime, tuple(s for s in kept if s.name in servers)

    def _new_runtime(self) -> MCPRuntime:
        from mistralai_vibe_local_harness.vibe import MCPDescriptorCachePolicy
        from mistralai_vibe_local_harness.vibe._mcp_runtime import MCPRuntime
        from vibe.app_server._unified_harness_backend_adapter import (
            _HarnessMCPAuthorizationProviderAdapter,  # pyright: ignore[reportPrivateUsage]
        )

        return MCPRuntime(
            cache_root=self._cache_root,
            cache_policy=MCPDescriptorCachePolicy(),
            authorization_provider=_HarnessMCPAuthorizationProviderAdapter(
                self._authorization
            ),
        )


def _harness_catalog(catalog: ResolvedMCPCatalog) -> Any:
    from vibe.app_server._unified_harness_backend_adapter import (
        _harness_mcp_catalog,  # pyright: ignore[reportPrivateUsage]
    )

    return _harness_mcp_catalog(catalog, None)


def _read_only(annotations: Any) -> bool:
    return isinstance(annotations, Mapping) and annotations.get("readOnlyHint") is True


__all__ = [
    "CALL_TOOL",
    "LIST_TOOL",
    "LOCAL_TOOLS",
    "VIBE_BOT_LOCAL_TOOLS_METHOD",
    "VIBE_BOT_MCP_CACHE_DIR",
    "HarnessLocalMCP",
    "LocalMCP",
    "LocalToolDescriptor",
    "LocalToolFailure",
    "LocalToolResult",
    "LocalTools",
    "VibeBotLocalToolServer",
    "VibeBotLocalToolsParams",
    "VibeBotLocalToolsResponse",
    "local_tool_mode",
]
