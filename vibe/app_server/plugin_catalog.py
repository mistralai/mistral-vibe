"""The session-scoped plugin catalogue behind ``/plugins``.

Vibe-owned end to end. ``plugin/info`` stays exactly as it is: its flat
component list cannot attribute an MCP server, a connector or a hook to a
plugin, and the model that would carry the owner belongs to the shared
protocol spec.

Registered on ``HarnessProcess`` beside the MCP and connector catalogs, and
stateless like them: one process serves every session, so the session's own
plugins arrive per call, off the backend the request named.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from vibe.app_server._dispatch import DispatchResult, RequestFailure, method_not_found
from vibe.app_server._model import validate_wire
from vibe.app_server._plugins import (
    SessionPlugins,
    plugin_components_by_owner,
    plugin_issues,
)
from vibe.app_server._session_backend_port import SessionBackend
from vibe.app_server.models import (
    PluginCatalogComponent,
    PluginCatalogDropped,
    PluginCatalogEntry,
    PluginCatalogState,
    PluginComponent,
    PluginScope,
)
from vibe.app_server.protocol import (
    PluginCatalogReadParams,
    PluginCatalogReadResponse,
    ProtocolErrorCode,
)
from vibe.core.plugins import (
    PluginDescriptor,
    PluginRouteKey,
    PluginRouteStatus,
    PluginSnapshotEntry,
)

type RouteStatuses = Mapping[str, PluginRouteStatus]

_ALIASES = {"plugins/read": "plugin_catalog/read"}


@runtime_checkable
class SessionPluginCatalogBinding(Protocol):
    @property
    def session_plugins(self) -> SessionPlugins: ...

    @property
    def installed_plugin_roots(self) -> Mapping[str, Path]: ...


class PluginCatalogService:
    """Answer ``plugin_catalog/read`` from the plugins one session is running."""

    @staticmethod
    def handles(method: str) -> bool:
        return method in _ALIASES or method.startswith("plugin_catalog/")

    async def dispatch(
        self, method: str, raw_params: dict[str, Any], *, root: SessionBackend | None
    ) -> DispatchResult:
        if _ALIASES.get(method, method) != "plugin_catalog/read":
            raise method_not_found(method)
        params = validate_wire(PluginCatalogReadParams, raw_params)
        binding = _target(params.session_id, root)
        return DispatchResult(
            response=PluginCatalogReadResponse(
                plugins=catalog(binding.session_plugins, binding.installed_plugin_roots)
            )
        )


def catalog(
    plugins: SessionPlugins, installed: Mapping[str, Path]
) -> PluginCatalogState:
    """Project one session's bound plugins into the catalogue ``/plugins`` renders.

    Membership comes from the snapshot and detail from the resolution, the
    join ``plugin_info`` already performs. The two can disagree: a pinned entry
    whose checkout this resolve dropped still owns routes the session runs, so
    it is listed from snapshot fields alone rather than hidden.
    """
    resolved = {
        plugin.name: plugin for plugin in plugins.materialized.resolution.plugins
    }
    owners = {group.name: group.plugin_name for group in plugins.snapshot.tool_groups}
    drift = _drift_by_plugin(plugins.routes, owners)
    components = plugin_components_by_owner(plugins)
    return PluginCatalogState(
        plugins=[
            _entry(
                entry,
                resolved.get(entry.name),
                installed.get(entry.name),
                components.get(entry.name, ()),
                drift.get(entry.name, {}),
            )
            for entry in plugins.snapshot.plugins
        ],
        dropped=[
            PluginCatalogDropped(file=issue.file, message=issue.message)
            for issue in plugin_issues(plugins)
        ],
    )


def _target(
    session_id: str, root: SessionBackend | None
) -> SessionPluginCatalogBinding:
    if root is None or root.session_id != session_id:
        raise RequestFailure(
            ProtocolErrorCode.NOT_FOUND, f"Session not found: {session_id}"
        )
    if not isinstance(root, SessionPluginCatalogBinding):
        raise RequestFailure(
            ProtocolErrorCode.NOT_IMPLEMENTED,
            "The selected session backend resolves no plugins",
        )
    return root


def _drift_by_plugin(
    routes: Mapping[PluginRouteKey, PluginRouteStatus], owners: Mapping[str, str]
) -> Mapping[str, RouteStatuses]:
    """Attribute every route that moved to the plugin owning its group."""
    drifted: dict[str, dict[str, PluginRouteStatus]] = defaultdict(dict)
    for (group, function), status in routes.items():
        owner = owners.get(group)
        if owner is None or status == "live":
            continue
        drifted[owner][f"{group}.{function}"] = status
    return drifted


def _entry(
    entry: PluginSnapshotEntry,
    descriptor: PluginDescriptor | None,
    installed: Path | None,
    components: tuple[PluginComponent, ...],
    statuses: RouteStatuses,
) -> PluginCatalogEntry:
    return PluginCatalogEntry(
        name=entry.name,
        version=entry.version,
        source_format=entry.source_format,
        manifest_digest=entry.manifest_digest,
        description="" if descriptor is None else descriptor.description,
        author=None if descriptor is None else descriptor.author,
        # `SkillScope` spells the same three values the wire literal admits.
        scope=None if descriptor is None else cast(PluginScope, descriptor.scope.value),
        content_sha256=None if descriptor is None else descriptor.content_digest,
        # The pinned checkout, not the install path: a restored session
        # resolves from the read-only copy the Runtime rebuilt.
        pinned_root=None if descriptor is None else str(descriptor.root),
        installed_root=None if installed is None else str(installed),
        components=[_component(component, statuses) for component in components],
        drifted=len(statuses),
    )


def _component(
    component: PluginComponent, statuses: RouteStatuses
) -> PluginCatalogComponent:
    return PluginCatalogComponent(
        kind=component.kind,
        name=component.name,
        status=statuses.get(component.name) if component.kind == "tool" else None,
    )
