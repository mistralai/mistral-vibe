"""SDK-independent contracts for Runtime-owned connector execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Protocol

from pydantic import JsonValue

type JsonObject = dict[str, JsonValue]
type JsonSchema = bool | JsonObject
type ConnectorAuthAction = Literal["none", "oauth", "credentials_setup", "unknown"]
type ConnectorSourceStatus = Literal[
    "disabled", "connected", "needs_auth", "needs_setup", "unavailable"
]


@dataclass(frozen=True, slots=True)
class ResolvedConnectorTool:
    raw_name: str
    description: str | None
    input_schema: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "input_schema", MappingProxyType(dict(self.input_schema))
        )


@dataclass(frozen=True, slots=True)
class ResolvedConnector:
    raw_id: str
    alias: str
    display_name: str
    ready: bool
    auth_action: ConnectorAuthAction
    tools: tuple[ResolvedConnectorTool, ...]
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedConnectorCatalog:
    revision: str
    connectors: tuple[ResolvedConnector, ...]


@dataclass(frozen=True, slots=True)
class ResolvedConnectorSetting:
    alias: str
    disabled: bool
    disabled_tools: frozenset[str]


@dataclass(frozen=True, slots=True)
class ResolvedConnectorSelection:
    selection_revision: str
    enable_connectors: bool
    implicit_source_enabled: bool
    connector_settings: tuple[ResolvedConnectorSetting, ...]
    enabled_tools: tuple[str, ...]
    disabled_tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConnectorToolDescriptor:
    raw_connector_id: str
    alias: str
    remote_name: str
    group_name: str
    programmatic_name: str
    display_name: str
    description: str
    input_schema: JsonSchema
    enabled: bool


@dataclass(frozen=True, slots=True)
class ConnectorToolRoute:
    group_name: str
    tool_name: str
    raw_connector_id: str
    remote_tool_name: str
    catalog_revision: str
    route_revision: str


@dataclass(frozen=True, slots=True)
class ConnectorToolGroup:
    name: str
    description: str
    tools: tuple[ConnectorToolDescriptor, ...]


@dataclass(frozen=True, slots=True)
class ConnectorSourceState:
    raw_id: str
    alias: str
    display_name: str
    status: ConnectorSourceStatus
    tools: tuple[ConnectorToolDescriptor, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectorRouteSnapshot:
    catalog_revision: str
    selection_revision: str
    route_revision: str
    groups: tuple[ConnectorToolGroup, ...]
    routes: Mapping[tuple[str, str], ConnectorToolRoute]
    sources: tuple[ConnectorSourceState, ...]


@dataclass(frozen=True, slots=True)
class ConnectorNormalizedResult:
    content: tuple[JsonObject, ...] = ()
    structured_content: JsonValue = None
    meta: JsonObject | None = None
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class ConnectorAuthorizationRequiredSignal:
    raw_connector_id: str
    alias: str
    accepted_catalog_revision: str
    action: ConnectorAuthAction
    reason: Literal["needs_auth", "needs_setup", "gateway_rejected"]


type ConnectorRuntimeEventSink = Callable[
    [ConnectorAuthorizationRequiredSignal], Awaitable[None] | None
]


class ConnectorRuntimeFailure(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class ConnectorGateway(Protocol):
    async def call(
        self, *, raw_connector_id: str, remote_tool_name: str, arguments: JsonObject
    ) -> ConnectorNormalizedResult: ...

    async def aclose(self) -> None: ...


type ConnectorSnapshotAcceptor = Callable[[ConnectorRouteSnapshot], Awaitable[None]]


def empty_connector_snapshot() -> ConnectorRouteSnapshot:
    return ConnectorRouteSnapshot(
        catalog_revision="",
        selection_revision="",
        route_revision="",
        groups=(),
        routes=MappingProxyType({}),
        sources=(),
    )


__all__ = [
    "ConnectorAuthorizationRequiredSignal",
    "ConnectorGateway",
    "ConnectorNormalizedResult",
    "ConnectorRouteSnapshot",
    "ConnectorRuntimeEventSink",
    "ConnectorRuntimeFailure",
    "ConnectorSnapshotAcceptor",
    "ConnectorSourceState",
    "ConnectorSourceStatus",
    "ConnectorToolDescriptor",
    "ConnectorToolGroup",
    "ConnectorToolRoute",
    "ResolvedConnector",
    "ResolvedConnectorCatalog",
    "ResolvedConnectorSelection",
    "ResolvedConnectorSetting",
    "ResolvedConnectorTool",
    "empty_connector_snapshot",
]
