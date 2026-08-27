"""Legacy MCP execution authorization contracts owned below the app server."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class MCPAuthorizationRef:
    server_name: str
    server_fingerprint: str
    kind: Literal["none", "static", "oauth"]
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
    reason: Literal["missing", "expired", "rejected", "invalid"]
    descriptor_revision: str
    observed_connection_revision: str | None = None


type MCPAuthorizationResult = MCPAuthorizationSnapshot | MCPAuthorizationRequired


class MCPAuthorizationProvider(Protocol):
    async def resolve(
        self, reference: MCPAuthorizationRef
    ) -> MCPAuthorizationResult: ...

    async def reject(
        self,
        reference: MCPAuthorizationRef,
        *,
        observed_connection_revision: str,
        reason: Literal["http_unauthorized", "mcp_unauthorized"],
    ) -> MCPAuthorizationResult: ...


type MCPAuthorizationRequiredSink = Callable[
    [str, MCPAuthorizationRequired], Awaitable[None] | None
]


__all__ = [
    "MCPAuthorizationProvider",
    "MCPAuthorizationRef",
    "MCPAuthorizationRequired",
    "MCPAuthorizationRequiredSink",
    "MCPAuthorizationResult",
    "MCPAuthorizationSnapshot",
]
