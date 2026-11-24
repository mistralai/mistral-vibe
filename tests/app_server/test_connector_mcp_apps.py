from __future__ import annotations

from pydantic import ValidationError
import pytest

from mistralai_vibe_local_harness.vibe import (
    ResolvedConnector as HarnessResolvedConnector,
    ResolvedConnectorCatalog as HarnessResolvedConnectorCatalog,
    ResolvedConnectorSelection,
    ResolvedConnectorTool as HarnessResolvedConnectorTool,
)
from mistralai_vibe_local_harness.vibe._connector_naming import build_connector_snapshot
from vibe.app_server._connector_mcp_apps import SessionMcpApps
from vibe.app_server._session_backend_port import (
    ResolvedConnector,
    ResolvedConnectorCatalog,
    ResolvedConnectorTool,
)
from vibe.app_server.models import (
    EffectCallDisplay,
    GenericEffectDetail,
    McpAppReference,
    McpServerAppReference,
    PluginMcpAppReference,
    PublicEffectEntry,
    PublicEntryGenerationStatus,
    RunningEffectState,
)

_TOOL = "connector_charts.show_chart"
_APP = McpAppReference(
    resource_uri="ui://charts/chart",
    connector_id="charts-id",
    connector_name="charts",
    connector_display_name="Charts",
)


def _catalog(uri: str | None) -> ResolvedConnectorCatalog:
    return ResolvedConnectorCatalog(
        provider_fingerprint="provider",
        revision=f"catalog-{uri}",
        connectors=(
            ResolvedConnector(
                raw_id="charts-id",
                alias="charts",
                display_name="Charts",
                ready=True,
                auth_action="none",
                tools=(ResolvedConnectorTool("show_chart", None, {}, uri),),
            ),
        ),
    )


def _routes(apps: SessionMcpApps, catalog: ResolvedConnectorCatalog) -> None:
    snapshot = build_connector_snapshot(
        HarnessResolvedConnectorCatalog(
            revision=catalog.revision,
            connectors=tuple(
                HarnessResolvedConnector(
                    raw_id=connector.raw_id,
                    alias=connector.alias,
                    display_name=connector.display_name,
                    ready=connector.ready,
                    auth_action=connector.auth_action,
                    tools=tuple(
                        HarnessResolvedConnectorTool(tool.raw_name, None, {})
                        for tool in connector.tools
                    ),
                )
                for connector in catalog.connectors
            ),
        ),
        ResolvedConnectorSelection(
            selection_revision="selection",
            enable_connectors=True,
            implicit_source_enabled=True,
            connector_settings=(),
            enabled_tools=(),
            disabled_tools=(),
        ),
    )
    apps.update_routes(snapshot, catalog)


def _call(entry_id: str) -> PublicEffectEntry:
    return PublicEffectEntry(
        id=entry_id,
        session_id="session-1",
        created_at=1,
        updated_at=1,
        generation_status=PublicEntryGenerationStatus.IN_PROGRESS,
        title=_TOOL,
        detail=GenericEffectDetail(
            tool_name=_TOOL,
            display=EffectCallDisplay(
                summary=_TOOL, verb="Running", message=_TOOL, status_text=_TOOL
            ),
        ),
        state=RunningEffectState(),
    )


def _app_of(apps: SessionMcpApps, entry_id: str) -> McpAppReference | None:
    [entry] = apps.apply([_call(entry_id)])
    assert isinstance(entry, PublicEffectEntry)
    assert isinstance(entry.detail, GenericEffectDetail)
    app = entry.detail.mcp_app
    # A connector call's app is always a connector reference.
    assert app is None or isinstance(app, McpAppReference)
    return app


def test_a_call_keeps_the_app_it_was_shown_with() -> None:
    """*Prepare*: A session whose connector tool loses, then regains, its app.
    *Do*: Show one call before each catalog change, and show every call again.
    *Assert*: Each call keeps the app it was first shown with. Clients hold a
      completed call as immutable, so a catalog change must not patch it.
    """
    # Prepare
    apps = SessionMcpApps()
    _routes(apps, _catalog("ui://charts/chart"))

    # Do
    first = _app_of(apps, "effect-1")
    _routes(apps, _catalog(None))
    second = _app_of(apps, "effect-2")
    _routes(apps, _catalog("ui://charts/chart"))

    # Assert
    assert (first, second) == (_APP, None)
    assert (_app_of(apps, "effect-1"), _app_of(apps, "effect-2")) == (_APP, None)
    assert _app_of(apps, "effect-3") == _APP


_CONNECTOR_APP = {
    "resourceUri": "ui://charts/chart",
    "connectorId": "charts-id",
    "connectorName": "charts",
    "connectorDisplayName": "Charts",
}


@pytest.mark.parametrize(
    ("mcp_app", "expected"),
    [
        pytest.param(_CONNECTOR_APP, McpAppReference, id="connector-without-kind"),
        pytest.param(
            {
                "kind": "plugin",
                "resourceUri": "ui://acme/app",
                "pluginName": "acme",
                "server": "acme-mcp",
            },
            PluginMcpAppReference,
            id="plugin",
        ),
        pytest.param(
            {"kind": "mcp_server", "resourceUri": "ui://tools/app", "server": "tools"},
            McpServerAppReference,
            id="mcp-server",
        ),
        pytest.param(_CONNECTOR_APP | {"kind": "widget"}, None, id="unknown-kind"),
    ],
)
def test_an_effect_reads_its_app_by_kind(
    mcp_app: dict[str, str], expected: type | None
) -> None:
    """An app without ``kind`` is a connector's, as older servers sent it."""
    detail = {
        "toolName": _TOOL,
        "display": {
            "summary": _TOOL,
            "verb": "Running",
            "message": _TOOL,
            "statusText": _TOOL,
        },
        "mcpApp": mcp_app,
    }

    if expected is None:
        with pytest.raises(ValidationError):
            GenericEffectDetail.model_validate(detail)
        return
    assert type(GenericEffectDetail.model_validate(detail).mcp_app) is expected
