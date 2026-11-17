from __future__ import annotations

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
    return entry.detail.mcp_app


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


def test_disabled_session_never_attaches_an_app() -> None:
    """*Prepare*: A session with the ``mcp_apps`` feature flag off, whose
    connector tool still advertises a UI resource.
    *Do*: Publish the routes and show calls.
    *Assert*: No call carries an app, across catalog refreshes.
    """
    # Prepare
    apps = SessionMcpApps(enabled=False)
    _routes(apps, _catalog("ui://charts/chart"))

    # Do / Assert
    assert _app_of(apps, "effect-1") is None
    _routes(apps, _catalog("ui://charts/chart"))
    assert _app_of(apps, "effect-2") is None


def test_disabled_session_strips_an_app_already_on_the_entry() -> None:
    """*Prepare*: A disabled session and a call whose entry already carries an
    app (e.g. attached before the flag was read).
    *Do*: Project the history.
    *Assert*: The app is stripped so the client has nothing to render.
    """
    # Prepare
    apps = SessionMcpApps(enabled=False)
    call = _call("effect-1")
    with_app = call.model_copy(
        update={"detail": call.detail.model_copy(update={"mcp_app": _APP})}
    )

    # Do
    [entry] = apps.apply([with_app])

    # Assert
    assert isinstance(entry, PublicEffectEntry)
    assert isinstance(entry.detail, GenericEffectDetail)
    assert entry.detail.mcp_app is None
