"""Session-owned connector route planning and gateway execution."""

import asyncio
from collections.abc import Awaitable, Iterable

from mistralai_vibe_local_harness.vibe._connector_models import (
    ConnectorAuthorizationRequiredSignal,
    ConnectorGateway,
    ConnectorNormalizedResult,
    ConnectorRouteSnapshot,
    ConnectorRuntimeEventSink,
    ConnectorRuntimeFailure,
    ConnectorSnapshotAcceptor,
    ConnectorSourceState,
    JsonObject,
    ResolvedConnector,
    ResolvedConnectorCatalog,
    ResolvedConnectorSelection,
    empty_connector_snapshot,
)
from mistralai_vibe_local_harness.vibe._connector_naming import build_connector_snapshot


class ConnectorRuntime:
    def __init__(
        self,
        gateway: ConnectorGateway,
        *,
        event_sink: ConnectorRuntimeEventSink | None = None,
        claimed_groups: Iterable[str] = (),
    ) -> None:
        self._gateway = gateway
        self._event_sink = event_sink
        self._claimed_groups = frozenset(claimed_groups)
        self._catalog = ResolvedConnectorCatalog(revision="", connectors=())
        self._selection = ResolvedConnectorSelection(
            selection_revision="",
            enable_connectors=False,
            implicit_source_enabled=False,
            connector_settings=(),
            enabled_tools=(),
            disabled_tools=(),
        )
        self._snapshot = empty_connector_snapshot()
        self._source_overrides: dict[str, str] = {}
        self._suspended_tools: set[tuple[str, str]] = set()
        self._pending_authorization: dict[
            tuple[str, str], ConnectorAuthorizationRequiredSignal
        ] = {}
        self._accept_snapshot: ConnectorSnapshotAcceptor | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def snapshot(self) -> ConnectorRouteSnapshot:
        return self._snapshot

    def bind_snapshot_acceptor(self, acceptor: ConnectorSnapshotAcceptor) -> None:
        self._accept_snapshot = acceptor

    async def reconfigure(
        self,
        catalog: ResolvedConnectorCatalog,
        selection: ResolvedConnectorSelection,
        *,
        push_to_core: bool = True,
    ) -> ConnectorRouteSnapshot:
        """Plan the catalog and store the resulting route snapshot.

        With ``push_to_core`` false the snapshot is stored but not handed to the
        capability acceptor, so the routes become dispatchable without a Core
        capability reconfigure. This lets a caller ready the routes while Core is
        mid-turn (which rejects reconfigure) and publish them once Core is idle
        via ``push_snapshot_to_core``.
        """
        async with self._lock:
            self._guard_open()
            candidate = plan_connector_snapshot(
                catalog, selection, claimed_groups=self._claimed_groups
            )
            if push_to_core:
                await self._accept(candidate)
            self._catalog = catalog
            self._selection = selection
            self._source_overrides.clear()
            self._suspended_tools.clear()
            self._pending_authorization.clear()
            self._snapshot = candidate
            return candidate

    async def push_snapshot_to_core(self) -> None:
        """Hand the current route snapshot to the capability acceptor.

        Pairs with a ``reconfigure(push_to_core=False)`` to publish the readied
        routes to Core once it is idle.
        """
        async with self._lock:
            self._guard_open()
            await self._accept(self._snapshot)

    async def suspend(self, *, alias: str, tool_name: str | None) -> ConnectorRouteSnapshot:
        async with self._lock:
            self._guard_open()
            self._source(alias)
            source_overrides = dict(self._source_overrides)
            suspended_tools = set(self._suspended_tools)
            if tool_name is None:
                source_overrides[alias] = "disabled"
            else:
                if not any(tool.raw_name == tool_name for tool in self._connector(alias).tools):
                    raise ConnectorRuntimeFailure(
                        "connector_unknown_tool", "Connector tool is not in the accepted catalog"
                    )
                suspended_tools.add((alias, tool_name))
            candidate = build_connector_snapshot(
                self._catalog,
                self._selection,
                source_overrides=source_overrides,
                suspended_tools=suspended_tools,
                claimed_groups=self._claimed_groups,
            )
            await self._accept(candidate)
            self._source_overrides = source_overrides
            self._suspended_tools = suspended_tools
            self._snapshot = candidate
            return candidate

    async def execute(
        self,
        *,
        group_name: str,
        tool_name: str,
        arguments: JsonObject,
    ) -> ConnectorNormalizedResult:
        async with self._lock:
            self._guard_open()
            snapshot = self._snapshot
            route = snapshot.routes.get((group_name, tool_name))
            if route is None:
                raise ConnectorRuntimeFailure(
                    "connector_unknown_route", "Connector tool route is not available"
                )
            if (
                route.catalog_revision != snapshot.catalog_revision
                or route.route_revision != snapshot.route_revision
            ):
                raise ConnectorRuntimeFailure(
                    "connector_stale_route", "Connector tool route is stale"
                )
        try:
            return await self._gateway.call(
                raw_connector_id=route.raw_connector_id,
                remote_tool_name=route.remote_tool_name,
                arguments=arguments,
            )
        except asyncio.CancelledError:
            raise
        except ConnectorRuntimeFailure as exc:
            if exc.code != "connector_authorization_required":
                raise
            await self._quarantine_authorization_rejection(
                group_name=group_name,
                tool_name=tool_name,
                route_revision=route.route_revision,
            )
            raise

    async def publish_pending_authorization(self, *, group_name: str, tool_name: str) -> None:
        async with self._lock:
            signal = self._pending_authorization.pop((group_name, tool_name), None)
        if signal is None or self._event_sink is None:
            return
        emitted = self._event_sink(signal)
        if isinstance(emitted, Awaitable):
            await emitted

    def source(self, alias: str) -> ConnectorSourceState:
        return self._source(alias)

    async def aclose(self) -> None:
        async with self._lock:
            if self._closed:
                return
            self._closed = True
            self._pending_authorization.clear()
        await self._gateway.aclose()

    async def _quarantine_authorization_rejection(
        self, *, group_name: str, tool_name: str, route_revision: str
    ) -> None:
        async with self._lock:
            self._guard_open()
            current = self._snapshot.routes.get((group_name, tool_name))
            if current is None or current.route_revision != route_revision:
                return
            connector = next(
                item for item in self._catalog.connectors if item.raw_id == current.raw_connector_id
            )
            source_overrides = {**self._source_overrides, connector.alias: "needs_auth"}
            candidate = build_connector_snapshot(
                self._catalog,
                self._selection,
                source_overrides=source_overrides,
                suspended_tools=self._suspended_tools,
                claimed_groups=self._claimed_groups,
            )
            await self._accept(candidate)
            self._source_overrides = source_overrides
            self._snapshot = candidate
            self._pending_authorization[(group_name, tool_name)] = (
                ConnectorAuthorizationRequiredSignal(
                    raw_connector_id=connector.raw_id,
                    alias=connector.alias,
                    accepted_catalog_revision=self._catalog.revision,
                    action=connector.auth_action,
                    reason="gateway_rejected",
                )
            )

    def _connector(self, alias: str) -> ResolvedConnector:
        for connector in self._catalog.connectors:
            if connector.alias == alias:
                return connector
        raise ConnectorRuntimeFailure(
            "connector_unknown_source", "Connector is not in the accepted catalog"
        )

    def _source(self, alias: str) -> ConnectorSourceState:
        for source in self._snapshot.sources:
            if source.alias == alias:
                return source
        raise ConnectorRuntimeFailure(
            "connector_unknown_source", "Connector is not in the accepted catalog"
        )

    def _guard_open(self) -> None:
        if self._closed:
            raise ConnectorRuntimeFailure("connector_runtime_closed", "Connector Runtime is closed")

    async def _accept(self, snapshot: ConnectorRouteSnapshot) -> None:
        if self._accept_snapshot is not None:
            # The acceptor owns DurableSessionRuntime's distinct lock, and its
            # capability transition is required to be action-free.
            await self._accept_snapshot(snapshot)


def plan_connector_snapshot(
    catalog: ResolvedConnectorCatalog,
    selection: ResolvedConnectorSelection,
    *,
    claimed_groups: frozenset[str] = frozenset(),
) -> ConnectorRouteSnapshot:
    """Build a validated candidate without making it the session's accepted snapshot."""
    _validate_configuration(catalog, selection)
    return build_connector_snapshot(catalog, selection, claimed_groups=claimed_groups)


def _validate_configuration(
    catalog: ResolvedConnectorCatalog, selection: ResolvedConnectorSelection
) -> None:
    raw_ids: set[str] = set()
    aliases: set[str] = set()
    for connector in catalog.connectors:
        if not connector.raw_id or connector.raw_id in raw_ids:
            raise ConnectorRuntimeFailure(
                "connector_invalid_configuration",
                "Connector raw IDs must be non-empty and unique",
            )
        if not connector.alias or connector.alias in aliases:
            raise ConnectorRuntimeFailure(
                "connector_invalid_configuration",
                "Connector aliases must be non-empty and unique",
            )
        raw_ids.add(connector.raw_id)
        aliases.add(connector.alias)
        tool_names = [tool.raw_name for tool in connector.tools]
        if any(not name for name in tool_names) or len(tool_names) != len(set(tool_names)):
            raise ConnectorRuntimeFailure(
                "connector_invalid_configuration",
                "Connector tool names must be non-empty and unique per source",
            )
    setting_aliases = [setting.alias for setting in selection.connector_settings]
    if len(setting_aliases) != len(set(setting_aliases)):
        raise ConnectorRuntimeFailure(
            "connector_invalid_configuration", "Connector settings must be unique"
        )


__all__ = ["ConnectorRuntime", "plan_connector_snapshot"]
