"""Measuring reconnected tool sources against the fingerprints a pin recorded."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from vibe.core.plugins._snapshot import PluginSourceKind, build_plugin_snapshot

if TYPE_CHECKING:
    from vibe.core.plugins._materialize import MaterializedPluginSet
    from vibe.core.plugins._snapshot import (
        PluginToolGroupSnapshot,
        PluginToolRouteSnapshot,
        PluginToolSnapshot,
        ResolvedPluginSnapshot,
    )

type PluginRouteStatus = Literal["live", "stale", "unavailable"]
type PluginRouteKey = tuple[str, str]
type PluginSourceRef = tuple[str, PluginSourceKind, str]


def route_key(route: PluginToolRouteSnapshot) -> PluginRouteKey:
    """The ``(group_name, function_name)`` pair ``tool_routes`` is keyed by."""
    return (route.group_name, route.function_name)


def plugin_route_source(route: PluginToolRouteSnapshot) -> PluginSourceRef:
    """The plugin, source kind, and source id a pinned route executes against."""
    return (route.plugin_name, route.source_kind, route.source_id)


def reconcile_plugin_routes(
    pinned: ResolvedPluginSnapshot, materialized: MaterializedPluginSet
) -> Mapping[PluginRouteKey, PluginRouteStatus]:
    """Status every pinned route against the sources that just answered.

    ``live`` is an unchanged fingerprint, ``stale`` is a source that answered
    with a different one or without the tool at all, and ``unavailable`` is a
    source that never answered. Only pinned routes are statused: a restored
    catalogue may shrink in meaning but never grow in membership.
    """
    answered = _answered_sources(materialized)
    derived = {
        key: route.schema_fingerprint for key, route in materialized.tool_routes.items()
    }
    statuses: dict[PluginRouteKey, PluginRouteStatus] = {}
    for route in pinned.tool_routes:
        key = route_key(route)
        if plugin_route_source(route) not in answered:
            statuses[key] = "unavailable"
        elif derived.get(key) == route.schema_fingerprint:
            statuses[key] = "live"
        else:
            statuses[key] = "stale"
    return MappingProxyType(statuses)


def retain_pinned_tools(
    current: ResolvedPluginSnapshot, previous: ResolvedPluginSnapshot
) -> ResolvedPluginSnapshot:
    """Carry tools a re-pin no longer derives into the catalogue it produced.

    Dropping one would make a tool the conversation has already called read as
    never having existed. Carried forward, `reconcile_plugin_routes` marks it
    ``unavailable`` and calling it says so. Tools only: no other component kind
    has a status on the wire, so a retained skill would be published as though
    it were still on disk.

    Returns ``current`` itself when nothing was lost.
    """
    derived = {route_key(route) for route in current.tool_routes}
    lost_routes = [
        route for route in previous.tool_routes if route_key(route) not in derived
    ]
    if not lost_routes:
        return current

    lost_keys = {route_key(route) for route in lost_routes}
    current_groups = {group.name: group for group in current.tool_groups}
    groups = list(current.tool_groups)
    for pinned_group in previous.tool_groups:
        retained = tuple(
            tool
            for tool in pinned_group.tools
            if (pinned_group.name, tool.name) in lost_keys
        )
        if retained:
            groups = _with_retained_tools(
                groups, current_groups.get(pinned_group.name), pinned_group, retained
            )

    # A carried route's owner has to stay named, or the snapshot rejects the
    # catalogue for pointing at a plugin it does not list.
    unnamed_owners = {route.plugin_name for route in lost_routes} - {
        entry.name for entry in current.plugins
    }
    return build_plugin_snapshot(
        (
            *current.plugins,
            *(entry for entry in previous.plugins if entry.name in unnamed_owners),
        ),
        skills=current.skills,
        knowledge=current.knowledge,
        agents=current.agents,
        hooks=current.hooks,
        libraries=current.libraries,
        connectors=current.connectors,
        tool_groups=groups,
        tool_routes=(*current.tool_routes, *lost_routes),
    )


def _answered_sources(materialized: MaterializedPluginSet) -> set[PluginSourceRef]:
    """Keyed by kind too: one plugin may declare both under one source id."""
    answered: set[PluginSourceRef] = set()
    for plugin, source in materialized.connected_mcp_sources:
        answered.add((plugin, "mcp", source))
    for plugin, source in materialized.connected_connector_sources:
        answered.add((plugin, "connector", source))
    return answered


def _with_retained_tools(
    groups: list[PluginToolGroupSnapshot],
    current_group: PluginToolGroupSnapshot | None,
    pinned_group: PluginToolGroupSnapshot,
    retained: tuple[PluginToolSnapshot, ...],
) -> list[PluginToolGroupSnapshot]:
    """Merge into the group of that name, or re-add the group the pin described."""
    if current_group is None:
        return [*groups, pinned_group.model_copy(update={"tools": retained})]
    merged = current_group.model_copy(
        update={"tools": (*current_group.tools, *retained)}
    )
    return [merged if group is current_group else group for group in groups]


__all__ = [
    "PluginRouteKey",
    "PluginRouteStatus",
    "PluginSourceRef",
    "plugin_route_source",
    "reconcile_plugin_routes",
    "retain_pinned_tools",
    "route_key",
]
