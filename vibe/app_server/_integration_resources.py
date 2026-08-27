from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from vibe.app_server._model import validate_wire
from vibe.app_server._streaming import (
    BoundedEventQueue,
    finish_event_queue,
    stream_request,
)
from vibe.app_server.client_state import ClientSessionState
from vibe.app_server.connection import AppServerResourceConnection
from vibe.app_server.models import (
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
    MCPToolSummary,
    PluginCatalogState,
    PublicError,
    TeleportComplete,
    TeleportEvent,
    TeleportFailed,
    VibeCodePickerView,
    VibeCodeProject,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    ConnectorAuthReadParams,
    ConnectorAuthReadResponse,
    ConnectorCatalogMutationResponse,
    ConnectorCatalogReadParams,
    ConnectorCatalogReadResponse,
    ConnectorCatalogRefreshParams,
    ConnectorCatalogToggleParams,
    ConnectorRefreshParams,
    ConnectorRefreshResponse,
    EmptyResponse,
    MCPAddParams,
    MCPAddResponse,
    MCPAuthUrlParams,
    MCPCatalogMutationResponse,
    MCPLoginParams,
    MCPLogoutParams,
    MCPReadParams,
    MCPReadResponse,
    MCPRefreshParams,
    MCPToggleParams,
    Notification,
    PluginCatalogReadParams,
    PluginCatalogReadResponse,
    PluginReloadParams,
    PluginReloadResponse,
    ProtocolErrorCode,
    RuntimeSnapshot,
    TeleportCancelParams,
    TeleportCancelResponse,
    TeleportEventParams,
    TeleportPushRespondParams,
    TeleportStartParams,
    TeleportStartResponse,
    VibeCodeProjectCancelParams,
    VibeCodeProjectCreateParams,
    VibeCodeProjectCreateResponse,
    VibeCodeProjectRecoverParams,
    VibeCodeProjectRecoverResponse,
    VibeCodeProjectSelectParams,
    VibeCodeProjectSelectResponse,
    VibeCodeProjectsLoadMoreParams,
    VibeCodeProjectsLoadMoreResponse,
    VibeCodeProjectsOpenParams,
    VibeCodeProjectsOpenResponse,
    VibeCodeProjectUnlinkParams,
    VibeCodeProjectUnlinkResponse,
)
from vibe.utils.mcp import MCPAddTransport

# A backend that resolves no plugins declines the read; a build that never
# registered the service has no such method at all. Both mean "no plugins here".
_NO_PLUGIN_BACKEND = frozenset({
    ProtocolErrorCode.NOT_IMPLEMENTED,
    ProtocolErrorCode.METHOD_NOT_FOUND,
})


def _required_mcp_runtime(runtime: RuntimeSnapshot | None) -> RuntimeSnapshot:
    if runtime is None:
        raise RuntimeError("A session-targeted MCP mutation returned no runtime")
    return runtime


def _connector_sources(
    response: ConnectorCatalogReadResponse,
) -> list[MCPSourceSummary]:
    if response.session is None:
        return []
    return [
        MCPSourceSummary(
            name=source.alias,
            kind=MCPSourceKind.CONNECTOR,
            transport="connector",
            status=MCPSourceStatus(source.status),
            tools=[
                MCPToolSummary(
                    name=tool.name,
                    description=tool.description or "",
                    enabled=tool.enabled,
                )
                for tool in source.tools
            ],
            error=source.error,
        )
        for source in response.session.sources
    ]


class MCPResource:
    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state
        self._login_events: dict[str, asyncio.Queue[MCPAuthUrlParams]] = {}

    @property
    def state(self) -> MCPState:
        return self._state.mcp

    async def read(self) -> MCPState:
        client = await self._connection.connect()
        mcp_response = validate_wire(
            MCPReadResponse,
            await client.request(
                "mcp_catalog/read", MCPReadParams(session_id=self._state.session_id)
            ),
        )
        try:
            connector_response = validate_wire(
                ConnectorCatalogReadResponse,
                await client.request(
                    "connector_catalog/read",
                    ConnectorCatalogReadParams(session_id=self._state.session_id),
                ),
            )
        except AppServerResponseError as exc:
            connectors = [
                source
                for source in self._state.mcp.sources
                if source.kind is MCPSourceKind.CONNECTOR
            ]
            connector_error = exc.error.message
        else:
            connectors = _connector_sources(connector_response)
            connector_error = (
                None
                if connector_response.catalog.disposition in {"memory", "fresh_cache"}
                else self._state.mcp.connector_error
            )

        connector_names = {source.name for source in connectors}
        connector_discovery_errors = {
            name: error
            for name, error in self._state.mcp.discovery_errors.items()
            if name in connector_names
        }
        state = MCPState(
            sources=[
                *(
                    source
                    for source in mcp_response.mcp.sources
                    if source.kind is MCPSourceKind.SERVER
                ),
                *connectors,
            ],
            discovery_errors={
                **mcp_response.mcp.discovery_errors,
                **connector_discovery_errors,
            },
            connector_error=connector_error,
        )
        self._state.mcp = state
        return state

    async def refresh(self) -> MCPState:
        client = await self._connection.connect()
        response = validate_wire(
            MCPCatalogMutationResponse,
            await client.request(
                "mcp_catalog/refresh",
                MCPRefreshParams(session_id=self._state.session_id),
            ),
        )
        runtime = _required_mcp_runtime(response.runtime)
        self._state.apply_runtime(runtime)
        return runtime.mcp

    async def refresh_connectors(self) -> MCPState:
        client = await self._connection.connect()
        response = validate_wire(
            ConnectorCatalogMutationResponse,
            await client.request(
                "connector_catalog/refresh",
                ConnectorCatalogRefreshParams(session_id=self._state.session_id),
            ),
        )
        if response.runtime is not None:
            self._state.apply_runtime(response.runtime)
        return self._state.mcp

    async def toggle(
        self,
        name: str,
        *,
        source: Literal["server", "connector"],
        disabled: bool,
        tool_name: str | None = None,
    ) -> MCPState:
        client = await self._connection.connect()
        if source == "connector":
            connector_response = validate_wire(
                ConnectorCatalogMutationResponse,
                await client.request(
                    "connector_catalog/toggle",
                    ConnectorCatalogToggleParams(
                        session_id=self._state.session_id,
                        alias=name,
                        disabled=disabled,
                        tool_name=tool_name,
                    ),
                ),
            )
            runtime = _required_mcp_runtime(connector_response.runtime)
            self._state.apply_runtime(runtime)
            return runtime.mcp
        response = validate_wire(
            MCPCatalogMutationResponse,
            await client.request(
                "mcp_catalog/toggle",
                MCPToggleParams(
                    session_id=self._state.session_id,
                    name=name,
                    source=source,
                    disabled=disabled,
                    tool_name=tool_name,
                ),
            ),
        )
        runtime = _required_mcp_runtime(response.runtime)
        self._state.apply_runtime(runtime)
        return runtime.mcp

    async def add(
        self,
        *,
        url: str,
        name: str | None,
        scopes: list[str],
        transport: MCPAddTransport,
    ) -> MCPAddResponse:
        client = await self._connection.connect()
        response = validate_wire(
            MCPAddResponse,
            await client.request(
                "mcp_catalog/add",
                MCPAddParams(
                    session_id=self._state.session_id,
                    url=url,
                    name=name,
                    scopes=scopes,
                    transport=transport,
                ),
            ),
        )
        self._state.apply_runtime(_required_mcp_runtime(response.runtime))
        return response

    async def logout(self, name: str) -> MCPState:
        client = await self._connection.connect()
        response = validate_wire(
            MCPCatalogMutationResponse,
            await client.request(
                "mcp_catalog/logout",
                MCPLogoutParams(session_id=self._state.session_id, name=name),
            ),
        )
        runtime = _required_mcp_runtime(response.runtime)
        self._state.apply_runtime(runtime)
        return runtime.mcp

    async def login(self, name: str) -> AsyncGenerator[MCPAuthUrlParams, None]:
        if name in self._login_events:
            raise RuntimeError(f"MCP login already in progress: {name}")
        client = await self._connection.connect()
        events = BoundedEventQueue[MCPAuthUrlParams]()
        self._login_events[name] = events
        try:
            async for event in stream_request(
                client,
                "mcp_catalog/login",
                MCPLoginParams(session_id=self._state.session_id, name=name),
                events,
                MCPCatalogMutationResponse,
            ):
                if isinstance(event, MCPCatalogMutationResponse):
                    self._state.apply_runtime(_required_mcp_runtime(event.runtime))
                else:
                    yield event
        finally:
            self._login_events.pop(name, None)

    async def consume_notification(self, notification: Notification) -> bool:
        if notification.method == "mcp/authUrl":
            # The catalog publishes the compatibility notification alongside the
            # canonical one. Current clients consume it without duplicating the URL.
            return True
        if notification.method != "mcp_catalog/authUrl":
            return False
        params = validate_wire(MCPAuthUrlParams, notification.params)
        if events := self._login_events.get(params.name):
            await events.put(params)
        return True

    async def connector_auth_url(self, name: str) -> str | None:
        client = await self._connection.connect()
        response = validate_wire(
            ConnectorAuthReadResponse,
            await client.request(
                "connectors/auth/read",
                ConnectorAuthReadParams(session_id=self._state.session_id, name=name),
            ),
        )
        return response.url

    async def refresh_connector(self, name: str) -> int:
        client = await self._connection.connect()
        response = validate_wire(
            ConnectorRefreshResponse,
            await client.request(
                "connectors/refresh",
                ConnectorRefreshParams(session_id=self._state.session_id, name=name),
            ),
        )
        self._state.apply_runtime(response.runtime)
        return response.tool_count


@dataclass(frozen=True, slots=True)
class PluginCatalogChange:
    """One plugin whose pinned content moved across a reload."""

    name: str
    before: str | None
    after: str | None


@dataclass(frozen=True, slots=True)
class PluginCatalogDiff:
    changes: tuple[PluginCatalogChange, ...]
    state: PluginCatalogState
    """The catalogue after the reload, so a re-render costs no third read."""


def _plugin_changes(
    before: PluginCatalogState, after: PluginCatalogState
) -> tuple[PluginCatalogChange, ...]:
    """Compare two catalogues on content digest, which is what a re-pin moves."""
    pinned = {entry.name: entry.content_sha256 for entry in before.plugins}
    current = {entry.name: entry.content_sha256 for entry in after.plugins}
    return tuple(
        PluginCatalogChange(name=name, before=pinned.get(name), after=current.get(name))
        for name in sorted(pinned.keys() | current.keys())
        if pinned.get(name) != current.get(name)
    )


class PluginCatalogResource:
    """The session's plugin catalogue, and the one command that re-pins it.

    Whether a backend resolves plugins at all is discovered rather than
    configured: ``--experimental-harness`` is a CLI argument that never reaches
    a client, and a client talking to ``vibe-app-server`` was never given one.
    So the first read answers the question and ``supported`` remembers it.
    """

    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state
        self._supported = False

    @property
    def supported(self) -> bool:
        """Whether a read has found a backend that resolves plugins."""
        return self._supported

    async def read(self) -> PluginCatalogState | None:
        """The catalogue, or ``None`` from a backend that resolves no plugins."""
        client = await self._connection.connect()
        try:
            response = validate_wire(
                PluginCatalogReadResponse,
                await client.request(
                    "plugin_catalog/read",
                    PluginCatalogReadParams(session_id=self._state.session_id),
                ),
            )
        except AppServerResponseError as exc:
            if exc.error.code not in _NO_PLUGIN_BACKEND:
                raise
            self._supported = False
            return None
        self._supported = True
        return response.plugins

    async def reload(self) -> PluginCatalogDiff | None:
        """Re-pin the session and report what moved, by digest.

        The diff is two reads either side of ``plugin/reload`` rather than a
        field on its response: the before-image belongs to whoever is about to
        render it, not to a Host holding one per session.
        """
        before = await self.read()
        if before is None:
            return None
        client = await self._connection.connect()
        validate_wire(
            PluginReloadResponse,
            await client.request(
                "plugin/reload", PluginReloadParams(session_id=self._state.session_id)
            ),
        )
        after = await self.read()
        if after is None:
            return None
        return PluginCatalogDiff(changes=_plugin_changes(before, after), state=after)


class VibeCodeResource:
    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state
        self._picker_id: str | None = None
        self._events: dict[str, asyncio.Queue[TeleportEvent]] = {}

    async def open_projects(
        self, *, for_teleport: bool = False, prompt: str | None = None
    ) -> tuple[VibeCodePickerView, str | None]:
        client = await self._connection.connect()
        response = validate_wire(
            VibeCodeProjectsOpenResponse,
            await client.request(
                "vibeCode/projects/open",
                VibeCodeProjectsOpenParams(
                    session_id=self._state.session_id,
                    purpose="teleport" if for_teleport else "configure",
                    prompt=prompt,
                ),
            ),
        )
        self._picker_id = response.picker_id
        return response.view, response.resolved_project_id

    async def load_more(self) -> tuple[VibeCodePickerView, str | None]:
        picker_id = self._require_picker_id()
        client = await self._connection.connect()
        response = validate_wire(
            VibeCodeProjectsLoadMoreResponse,
            await client.request(
                "vibeCode/projects/loadMore",
                VibeCodeProjectsLoadMoreParams(
                    session_id=self._state.session_id, picker_id=picker_id
                ),
            ),
        )
        return response.view, response.focus_option_id

    async def create(
        self, *, name: str, default_branch: str
    ) -> tuple[VibeCodePickerView, VibeCodeProject]:
        picker_id = self._require_picker_id()
        client = await self._connection.connect()
        response = validate_wire(
            VibeCodeProjectCreateResponse,
            await client.request(
                "vibeCode/projects/create",
                VibeCodeProjectCreateParams(
                    session_id=self._state.session_id,
                    picker_id=picker_id,
                    name=name,
                    default_branch=default_branch,
                ),
            ),
        )
        return response.view, response.project

    async def select_project(
        self, project_id: str
    ) -> tuple[VibeCodePickerView, VibeCodeProject]:
        picker_id = self._require_picker_id()
        client = await self._connection.connect()
        response = validate_wire(
            VibeCodeProjectSelectResponse,
            await client.request(
                "vibeCode/projects/select",
                VibeCodeProjectSelectParams(
                    session_id=self._state.session_id,
                    picker_id=picker_id,
                    project_id=project_id,
                ),
            ),
        )
        return response.view, response.project

    async def unlink(self) -> VibeCodePickerView:
        picker_id = self._require_picker_id()
        client = await self._connection.connect()
        response = validate_wire(
            VibeCodeProjectUnlinkResponse,
            await client.request(
                "vibeCode/projects/unlink",
                VibeCodeProjectUnlinkParams(
                    session_id=self._state.session_id, picker_id=picker_id
                ),
            ),
        )
        return response.view

    async def cancel_picker(self) -> None:
        picker_id = self._require_picker_id()
        client = await self._connection.connect()
        validate_wire(
            EmptyResponse,
            await client.request(
                "vibeCode/projects/cancel",
                VibeCodeProjectCancelParams(
                    session_id=self._state.session_id, picker_id=picker_id
                ),
            ),
        )
        self._picker_id = None

    async def recover_stale_link(self) -> tuple[VibeCodePickerView, bool]:
        picker_id = self._require_picker_id()
        client = await self._connection.connect()
        response = validate_wire(
            VibeCodeProjectRecoverResponse,
            await client.request(
                "vibeCode/projects/recover",
                VibeCodeProjectRecoverParams(
                    session_id=self._state.session_id, picker_id=picker_id
                ),
            ),
        )
        return response.view, response.recovered

    async def teleport(
        self, prompt: str | None, *, project_id: str
    ) -> AsyncGenerator[TeleportEvent, None]:
        picker_id = self._require_picker_id()
        client = await self._connection.connect()
        operation_id = str(uuid4())
        events = BoundedEventQueue[TeleportEvent]()
        self._events[operation_id] = events
        request_sent = False
        finished = False
        try:
            request_sent = True
            validate_wire(
                TeleportStartResponse,
                await client.request(
                    "vibeCode/teleport/start",
                    TeleportStartParams(
                        session_id=self._state.session_id,
                        picker_id=picker_id,
                        operation_id=operation_id,
                        prompt=prompt,
                        project_id=project_id,
                    ),
                ),
            )
            while True:
                event = await events.get()
                terminal = isinstance(event, TeleportComplete | TeleportFailed)
                if terminal:
                    finished = True
                yield event
                if terminal:
                    return
        finally:
            self._events.pop(operation_id, None)
            if request_sent and not finished:
                with suppress(Exception):
                    await self._cancel_teleport(operation_id)

    def _require_picker_id(self) -> str:
        if self._picker_id is None:
            raise RuntimeError("Vibe Code project picker is not open")
        return self._picker_id

    def reset(self) -> None:
        self._picker_id = None
        for operation_id, events in self._events.items():
            finish_event_queue(
                events,
                TeleportFailed(
                    operation_id=operation_id,
                    error=PublicError(
                        message="Teleport was cancelled because the session changed",
                        code="session_changed",
                    ),
                ),
            )

    async def _cancel_teleport(self, operation_id: str) -> None:
        client = await self._connection.connect()
        validate_wire(
            TeleportCancelResponse,
            await client.request(
                "vibeCode/teleport/cancel",
                TeleportCancelParams(
                    session_id=self._state.session_id, operation_id=operation_id
                ),
            ),
        )

    async def respond_to_push(self, operation_id: str, *, approved: bool) -> None:
        client = await self._connection.connect()
        validate_wire(
            EmptyResponse,
            await client.request(
                "vibeCode/teleport/push/respond",
                TeleportPushRespondParams(
                    session_id=self._state.session_id,
                    operation_id=operation_id,
                    approved=approved,
                ),
            ),
        )

    async def consume_notification(self, notification: Notification) -> bool:
        if notification.method != "vibeCode/teleport/event":
            return False
        params = validate_wire(TeleportEventParams, notification.params)
        if events := self._events.get(params.event.operation_id):
            await events.put(params.event)
        return True
