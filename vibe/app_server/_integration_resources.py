from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import suppress
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
    MCPState,
    PublicError,
    TeleportComplete,
    TeleportEvent,
    TeleportFailed,
    VibeCodePickerView,
    VibeCodeProject,
)
from vibe.app_server.protocol import (
    ConnectorAuthReadParams,
    ConnectorAuthReadResponse,
    ConnectorRefreshParams,
    ConnectorRefreshResponse,
    EmptyResponse,
    MCPAddParams,
    MCPAddResponse,
    MCPAuthUrlParams,
    MCPLoginParams,
    MCPLogoutParams,
    MCPReadParams,
    MCPReadResponse,
    MCPRefreshParams,
    MCPToggleParams,
    Notification,
    RuntimeMutationResponse,
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
        response = validate_wire(
            MCPReadResponse,
            await client.request(
                "mcp/read", MCPReadParams(session_id=self._state.session_id)
            ),
        )
        self._state.mcp = response.mcp
        return response.mcp

    async def refresh(self) -> MCPState:
        client = await self._connection.connect()
        response = validate_wire(
            RuntimeMutationResponse,
            await client.request(
                "mcp/refresh", MCPRefreshParams(session_id=self._state.session_id)
            ),
        )
        self._state.apply_runtime(response.runtime)
        return response.runtime.mcp

    async def toggle(
        self,
        name: str,
        *,
        source: Literal["server", "connector"],
        disabled: bool,
        tool_name: str | None = None,
    ) -> MCPState:
        client = await self._connection.connect()
        response = validate_wire(
            RuntimeMutationResponse,
            await client.request(
                "mcp/toggle",
                MCPToggleParams(
                    session_id=self._state.session_id,
                    name=name,
                    source=source,
                    disabled=disabled,
                    tool_name=tool_name,
                ),
            ),
        )
        self._state.apply_runtime(response.runtime)
        return response.runtime.mcp

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
                "mcp/add",
                MCPAddParams(
                    session_id=self._state.session_id,
                    url=url,
                    name=name,
                    scopes=scopes,
                    transport=transport,
                ),
            ),
        )
        self._state.apply_runtime(response.runtime)
        return response

    async def logout(self, name: str) -> MCPState:
        client = await self._connection.connect()
        response = validate_wire(
            RuntimeMutationResponse,
            await client.request(
                "mcp/logout",
                MCPLogoutParams(session_id=self._state.session_id, name=name),
            ),
        )
        self._state.apply_runtime(response.runtime)
        return response.runtime.mcp

    async def login(self, name: str) -> AsyncGenerator[MCPAuthUrlParams, None]:
        if name in self._login_events:
            raise RuntimeError(f"MCP login already in progress: {name}")
        client = await self._connection.connect()
        events = BoundedEventQueue[MCPAuthUrlParams]()
        self._login_events[name] = events
        try:
            async for event in stream_request(
                client,
                "mcp/login",
                MCPLoginParams(session_id=self._state.session_id, name=name),
                events,
                RuntimeMutationResponse,
            ):
                if isinstance(event, RuntimeMutationResponse):
                    self._state.apply_runtime(event.runtime)
                else:
                    yield event
        finally:
            self._login_events.pop(name, None)

    async def consume_notification(self, notification: Notification) -> bool:
        if notification.method != "mcp/authUrl":
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
