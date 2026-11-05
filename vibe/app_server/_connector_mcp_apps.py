"""MCP Apps of connector tools (SEP-1865).

A connector tool can declare, in its `_meta.ui`, a `ui://` resource that
renders its calls, and whether the model, the app, or both may call it. The
catalog keeps that resource for each tool; a session names, on each call to
one of its connector routes, the app that renders it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from pydantic import JsonValue

from vibe.app_server._session_backend_port import ResolvedConnectorCatalog
from vibe.app_server.models import (
    GenericEffectDetail,
    McpAppReference,
    PublicEffectEntry,
    PublicHistoryEntry,
)

if TYPE_CHECKING:
    # The catalog imports this module at startup; the Harness loads later.
    from mistralai_vibe_local_harness.vibe import ConnectorRouteSnapshot

_UI_URI_SCHEME: Final = "ui://"
_MODEL: Final = "model"
_APP: Final = "app"


@dataclass(frozen=True, slots=True)
class ConnectorToolUi:
    model_visible: bool
    resource_uri: str | None


def connector_tool_ui(meta: JsonValue) -> ConnectorToolUi:
    """Read a tool's `_meta` as the cloud Runtime does (`mcp_annotations.py`)."""
    visibility = _visibility(meta)
    return ConnectorToolUi(
        model_visible=_MODEL in visibility,
        resource_uri=_resource_uri(meta) if _APP in visibility else None,
    )


def connector_effect_tool_name(group_name: str, programmatic_name: str) -> str:
    """The tool name a connector call's effect carries (the Harness projection)."""
    return f"{group_name}.{programmatic_name}"


class SessionMcpApps:
    """The MCP App of each connector call in one session.

    An entry's app is decided once, when the session first shows the entry,
    and kept: a completed entry is immutable for clients, so a later catalog
    must not add or remove the app of a call it already showed.
    """

    def __init__(self) -> None:
        self._by_route: dict[str, McpAppReference] = {}
        self._by_entry: dict[str, McpAppReference | None] = {}

    def update_routes(
        self, snapshot: ConnectorRouteSnapshot, catalog: ResolvedConnectorCatalog
    ) -> None:
        uris = {
            (connector.raw_id, tool.raw_name): tool.ui_resource_uri
            for connector in catalog.connectors
            for tool in connector.tools
        }
        self._by_route = {
            connector_effect_tool_name(
                tool.group_name, tool.programmatic_name
            ): McpAppReference(
                resource_uri=uri,
                connector_id=source.raw_id,
                connector_name=source.alias,
                connector_display_name=source.display_name,
            )
            for source in snapshot.sources
            for tool in source.tools
            if (uri := uris.get((source.raw_id, tool.remote_name))) is not None
        }

    def apply(self, history: Sequence[PublicHistoryEntry]) -> list[PublicHistoryEntry]:
        return [self._with_app(entry) for entry in history]

    def _with_app(self, entry: PublicHistoryEntry) -> PublicHistoryEntry:
        if not isinstance(entry, PublicEffectEntry) or not isinstance(
            entry.detail, GenericEffectDetail
        ):
            return entry
        if entry.id not in self._by_entry:
            self._by_entry[entry.id] = self._by_route.get(entry.detail.tool_name)
        app = self._by_entry[entry.id]
        if app is None:
            return entry
        return entry.model_copy(
            update={"detail": entry.detail.model_copy(update={"mcp_app": app})}
        )


def _resource_uri(meta: JsonValue) -> str | None:
    if not isinstance(meta, dict):
        return None
    ui = meta.get("ui")
    uri = None
    if isinstance(ui, dict):
        uri = ui.get("resourceUri")
        if uri is None:
            uri = ui.get("resource_uri")
    if uri is None:
        uri = meta.get("ui/resourceUri")
    if not isinstance(uri, str):
        return None
    uri = uri.strip()
    if not uri.startswith(_UI_URI_SCHEME) or uri == _UI_URI_SCHEME:
        return None
    return uri


def _visibility(meta: JsonValue) -> frozenset[str]:
    """Who may call the tool. Absent or malformed means both, as in the spec."""
    both = frozenset({_MODEL, _APP})
    ui = meta.get("ui") if isinstance(meta, dict) else None
    declared = ui.get("visibility") if isinstance(ui, dict) else None
    if not isinstance(declared, list) or not all(
        isinstance(item, str) and item in both for item in declared
    ):
        return both
    return frozenset(item for item in declared if isinstance(item, str))


__all__ = [
    "ConnectorToolUi",
    "SessionMcpApps",
    "connector_effect_tool_name",
    "connector_tool_ui",
]
