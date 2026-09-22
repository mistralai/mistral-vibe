"""SDK-independent contracts for Unified Harness MCP execution."""

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from fnmatch import fnmatch
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Protocol

from pydantic import JsonValue

type JsonObject = dict[str, JsonValue]
type JsonSchema = bool | JsonObject
type MCPTransportKind = Literal["http", "streamable-http", "stdio"]
type MCPAuthorizationKind = Literal["none", "static", "oauth"]
type MCPAuthorizationReason = Literal["missing", "expired", "rejected", "invalid"]


@dataclass(frozen=True, slots=True)
class MCPAuthorizationRef:
    server_name: str
    server_fingerprint: str
    kind: MCPAuthorizationKind
    descriptor_revision: str


@dataclass(frozen=True, slots=True)
class MCPAuthorizationSnapshot:
    headers: Mapping[str, str] = field(repr=False)
    connection_revision: str
    descriptor_revision: str
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(frozen=True, slots=True)
class MCPAuthorizationRequired:
    reason: MCPAuthorizationReason
    descriptor_revision: str
    observed_connection_revision: str | None = None


type MCPAuthorizationResult = MCPAuthorizationSnapshot | MCPAuthorizationRequired


class MCPAuthorizationProvider(Protocol):
    async def resolve(self, reference: MCPAuthorizationRef) -> MCPAuthorizationResult: ...

    async def reject(
        self,
        reference: MCPAuthorizationRef,
        *,
        observed_connection_revision: str,
        reason: Literal["http_unauthorized", "mcp_unauthorized"],
    ) -> MCPAuthorizationResult: ...


@dataclass(frozen=True, slots=True)
class ResolvedMCPServerConfig:
    name: str
    transport: MCPTransportKind
    url: str | None = None
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: Path | None = None
    env: Mapping[str, str] = field(default_factory=dict, repr=False)
    authorization: MCPAuthorizationRef = field(
        default_factory=lambda: MCPAuthorizationRef("", "", "none", "")
    )
    prompt: str | None = None
    startup_timeout_s: float = 30.0
    tool_timeout_s: float = 300.0
    sampling_enabled: bool = True
    disabled: bool = False
    disabled_tools: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "env", MappingProxyType(dict(self.env)))


@dataclass(frozen=True, slots=True)
class MCPToolFilter:
    """Host-resolved global tool patterns, case-insensitive fnmatch globs or
    ``re:``-prefixed regex full matches.

    They match a tool's display name (``{server}_{tool}``), not its ``group.tool``
    route: a Host applies one glob list across every tool it publishes.
    """

    enabled_globs: tuple[str, ...] = ()
    disabled_globs: tuple[str, ...] = ()

    def allows(self, display_name: str) -> bool:
        if self.enabled_globs and not _pattern_matches(display_name, self.enabled_globs):
            return False
        return not (
            self.disabled_globs
            and _pattern_matches(display_name, self.disabled_globs, unparseable_matches=True)
        )


# An unparseable ``re:`` pattern cannot be evaluated, so the caller says which way to
# miss; both callers miss toward leaving the tool unpublished.
def _pattern_matches(
    name: str, patterns: tuple[str, ...], *, unparseable_matches: bool = False
) -> bool:
    lowered = name.lower()
    for raw in patterns:
        pattern = (raw or "").strip()
        if not pattern:
            continue
        if pattern.startswith("re:"):
            compiled = _compile_icase(pattern[3:])
            if compiled is None:
                if unparseable_matches:
                    return True
                continue
            if compiled.fullmatch(name):
                return True
        elif fnmatch(lowered, pattern.lower()):
            return True
    return False


@lru_cache(maxsize=256)
def _compile_icase(expression: str) -> re.Pattern[str] | None:
    try:
        return re.compile(expression, re.IGNORECASE)
    except re.error:
        return None


@dataclass(frozen=True, slots=True)
class ResolvedMCPCatalog:
    revision: str
    servers: tuple[ResolvedMCPServerConfig, ...]
    tool_filter: MCPToolFilter | None = None


@dataclass(frozen=True, slots=True)
class MCPRemoteToolDescriptor:
    remote_name: str
    description: str = ""
    input_schema: JsonSchema = field(default_factory=dict)
    output_schema: JsonSchema | None = None
    annotations: JsonValue = None


@dataclass(frozen=True, slots=True)
class MCPDescriptorCachePolicy:
    ttl_s: float = 86_400.0
    max_tools_per_record: int = 1_000
    max_record_bytes: int = 2 * 1024 * 1024
    max_files: int = 512
    max_directory_bytes: int = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MCPHTTPTransportPolicy:
    enable_system_trust_store: bool = False


@dataclass(frozen=True, slots=True)
class MCPDescriptorCacheKey:
    format_version: Literal[1]
    naming_version: str
    server_fingerprint: str
    authorization_descriptor_revision: str


@dataclass(frozen=True, slots=True)
class MCPDescriptorCacheRecordV1:
    key: MCPDescriptorCacheKey
    source_name: str
    discovered_at: datetime
    last_used_at: datetime
    descriptors: tuple[MCPRemoteToolDescriptor, ...]


@dataclass(frozen=True, slots=True)
class MCPToolDescriptor:
    server_name: str
    remote_name: str
    group_name: str
    programmatic_name: str
    display_name: str
    description: str
    input_schema: JsonSchema
    output_schema: JsonSchema | None
    annotations: JsonValue
    enabled: bool


@dataclass(frozen=True, slots=True)
class MCPToolRoute:
    group_name: str
    tool_name: str
    descriptor: MCPToolDescriptor
    server_fingerprint: str


@dataclass(frozen=True, slots=True)
class MCPToolGroup:
    name: str
    description: str
    tools: tuple[MCPToolDescriptor, ...]


@dataclass(frozen=True, slots=True)
class MCPSourceState:
    name: str
    status: Literal["disabled", "enabled", "connected", "needs_auth", "unavailable"]
    descriptors: tuple[MCPRemoteToolDescriptor, ...] = ()
    descriptor_revision: str = ""
    error: str | None = None


@dataclass(frozen=True, slots=True)
class MCPRouteSnapshot:
    catalog_revision: str
    route_revision: str
    groups: tuple[MCPToolGroup, ...]
    routes: Mapping[tuple[str, str], MCPToolRoute]
    sources: tuple[MCPSourceState, ...]


@dataclass(frozen=True, slots=True)
class MCPNormalizedResult:
    content: tuple[JsonObject, ...] = ()
    structured_content: JsonValue = None
    meta: JsonObject | None = None
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class MCPAuthorizationRequiredSignal:
    server_name: str
    reason: MCPAuthorizationReason
    descriptor_revision: str
    observed_connection_revision: str | None = None


type MCPRuntimeEventSink = Callable[[MCPAuthorizationRequiredSignal], Awaitable[None] | None]


class MCPRuntimeFailure(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class MCPAuthorizationRejected(MCPRuntimeFailure):
    def __init__(
        self,
        message: str,
        *,
        reason: Literal["http_unauthorized", "mcp_unauthorized"],
    ) -> None:
        self.reason: Literal["http_unauthorized", "mcp_unauthorized"] = reason
        super().__init__("mcp_authorization_required", message)


class MCPTransportDisconnected(MCPRuntimeFailure):
    def __init__(self, message: str) -> None:
        super().__init__("mcp_transport_disconnected", message, retryable=True)


class MCPHTTPStatusFailure(MCPRuntimeFailure):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__("mcp_http_error", message)


class MCPClientConnection(Protocol):
    async def list_tools(self) -> tuple[MCPRemoteToolDescriptor, ...]: ...

    async def call_tool(
        self, name: str, arguments: JsonObject, *, timeout_s: float
    ) -> MCPNormalizedResult: ...

    async def aclose(self) -> None: ...


type MCPSamplingCallback = Callable[[object, object], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class MCPSamplingMessage:
    role: Literal["user", "assistant"]
    text: str


@dataclass(frozen=True, slots=True)
class MCPSamplingRequest:
    messages: tuple[MCPSamplingMessage, ...]
    system_prompt: str | None
    temperature: float | None
    max_tokens: int


@dataclass(frozen=True, slots=True)
class MCPSamplingResponse:
    text: str
    model: str


type MCPSamplingCompletion = Callable[[MCPSamplingRequest], Awaitable[MCPSamplingResponse]]


class MCPTransportFactory(Protocol):
    async def open_stdio(
        self,
        server: ResolvedMCPServerConfig,
        *,
        sampling_callback: MCPSamplingCallback | None,
    ) -> MCPClientConnection: ...


__all__ = [
    "JsonObject",
    "JsonSchema",
    "MCPAuthorizationProvider",
    "MCPAuthorizationRef",
    "MCPAuthorizationRejected",
    "MCPAuthorizationRequired",
    "MCPAuthorizationRequiredSignal",
    "MCPAuthorizationResult",
    "MCPAuthorizationSnapshot",
    "MCPClientConnection",
    "MCPDescriptorCacheKey",
    "MCPDescriptorCachePolicy",
    "MCPDescriptorCacheRecordV1",
    "MCPHTTPTransportPolicy",
    "MCPNormalizedResult",
    "MCPRemoteToolDescriptor",
    "MCPRouteSnapshot",
    "MCPRuntimeEventSink",
    "MCPRuntimeFailure",
    "MCPSamplingCallback",
    "MCPSamplingCompletion",
    "MCPSamplingMessage",
    "MCPSamplingRequest",
    "MCPSamplingResponse",
    "MCPSourceState",
    "MCPToolDescriptor",
    "MCPToolFilter",
    "MCPToolGroup",
    "MCPToolRoute",
    "MCPTransportDisconnected",
    "MCPTransportFactory",
    "ResolvedMCPCatalog",
    "ResolvedMCPServerConfig",
]
