from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Literal

from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server._streaming import BoundedEventQueue, stream_request
from vibe.app_server.client_state import ClientSessionState
from vibe.app_server.connection import AppServerResourceConnection
from vibe.app_server.models import (
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
    MCPToolSummary,
    PluginCatalogState,
    SkillSummary,
    SkillUpdateView,
    SkillVersionView,
)
from vibe.app_server.protocol import (
    AppServerResponseError,
    ConnectorAuthReadParams,
    ConnectorAuthReadResponse,
    ConnectorCatalogConnectParams,
    ConnectorCatalogMutationResponse,
    ConnectorCatalogReadParams,
    ConnectorCatalogReadResponse,
    ConnectorCatalogRefreshParams,
    ConnectorCatalogToggleParams,
    ConnectorRefreshParams,
    ConnectorRefreshResponse,
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
    RuntimeMutationResponse,
    RuntimeSnapshot,
    SkillsCatalogParams,
    SkillsCatalogResponse,
    SkillsDetailParams,
    SkillsDetailResponse,
    SkillsImportParams,
    SkillsInstalledParams,
    SkillsInstalledResponse,
    SkillsRemoveParams,
    SkillsUpdatesParams,
    SkillsUpdatesResponse,
    SkillsVersionsParams,
    SkillsVersionsResponse,
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
            display_name=source.display_name,
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


class SkillsResource:
    """Client projection for registry skills browse/import/manage.

    Reads go through dedicated methods; mutations round-trip to the server (where
    the real config + SkillManager live), which rebuilds discovery + the system
    prompt and returns a fresh runtime snapshot we apply locally.
    """

    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state

    @property
    def installed(self) -> list[SkillSummary]:
        return self._state.skills

    async def read_installed(self) -> list[SkillSummary]:
        client = await self._connection.connect()
        response = validate_wire(
            SkillsInstalledResponse,
            await client.request(
                "skills/installed",
                SkillsInstalledParams(session_id=self._state.session_id),
            ),
        )
        return response.skills

    async def catalog(self) -> SkillsCatalogResponse:
        client = await self._connection.connect()
        return validate_wire(
            SkillsCatalogResponse,
            await client.request(
                "skills/catalog", SkillsCatalogParams(session_id=self._state.session_id)
            ),
        )

    async def updates(self) -> list[SkillUpdateView]:
        client = await self._connection.connect()
        response = validate_wire(
            SkillsUpdatesResponse,
            await client.request(
                "skills/updates", SkillsUpdatesParams(session_id=self._state.session_id)
            ),
        )
        return response.updates

    async def versions(self, skill_id: str) -> list[SkillVersionView]:
        client = await self._connection.connect()
        response = validate_wire(
            SkillsVersionsResponse,
            await client.request(
                "skills/versions",
                SkillsVersionsParams(
                    session_id=self._state.session_id, skill_id=skill_id
                ),
            ),
        )
        return response.versions

    async def detail(
        self, skill_id: str, *, version: int | None = None
    ) -> SkillsDetailResponse:
        client = await self._connection.connect()
        return validate_wire(
            SkillsDetailResponse,
            await client.request(
                "skills/detail",
                SkillsDetailParams(
                    session_id=self._state.session_id,
                    skill_id=skill_id,
                    version=version,
                ),
            ),
        )

    async def _mutate(self, method: str, params: ProtocolModel) -> list[SkillSummary]:
        client = await self._connection.connect()
        response = validate_wire(
            RuntimeMutationResponse, await client.request(method, params)
        )
        self._state.apply_runtime(response.runtime)
        return await self.read_installed()

    async def import_skill(
        self,
        skill_id: str,
        *,
        version: int | None = None,
        alias: str | None = None,
        scope: str = "global",
    ) -> list[SkillSummary]:
        return await self._mutate(
            "skills/import",
            SkillsImportParams(
                session_id=self._state.session_id,
                skill_id=skill_id,
                version=version,
                alias=alias,
                scope=scope,
            ),
        )

    async def remove(self, name: str, scope: str = "global") -> list[SkillSummary]:
        return await self._mutate(
            "skills/remove",
            SkillsRemoveParams(
                session_id=self._state.session_id, name=name, scope=scope
            ),
        )


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
            manage_connectors_url = self._state.mcp.manage_connectors_url
            private_connectors_hidden = self._state.mcp.private_connectors_hidden
        else:
            connectors = _connector_sources(connector_response)
            connector_error = (
                None
                if connector_response.catalog.disposition in {"memory", "fresh_cache"}
                else self._state.mcp.connector_error
            )
            manage_connectors_url = connector_response.manage_url
            private_connectors_hidden = connector_response.private_connectors_hidden

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
            manage_connectors_url=manage_connectors_url,
            private_connectors_hidden=private_connectors_hidden,
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
        return await self._mutate_connectors(
            "connector_catalog/refresh",
            ConnectorCatalogRefreshParams(session_id=self._state.session_id),
        )

    async def connect_connector(self, name: str) -> MCPState:
        return await self._mutate_connectors(
            "connector_catalog/connect",
            ConnectorCatalogConnectParams(
                session_id=self._state.session_id, alias=name
            ),
        )

    # An unchanged catalog answers with no runtime, so the state stands.
    async def _mutate_connectors(
        self,
        method: Literal["connector_catalog/refresh", "connector_catalog/connect"],
        params: ProtocolModel,
    ) -> MCPState:
        client = await self._connection.connect()
        response = validate_wire(
            ConnectorCatalogMutationResponse, await client.request(method, params)
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
        allow_insecure_http: bool = False,
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
                    allow_insecure_http=allow_insecure_http,
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

    async def login(self, name: str) -> AsyncGenerator[MCPAuthUrlParams]:
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
    """The session's plugin catalogue, and the one command that re-pins it."""

    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state

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
            return None
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
