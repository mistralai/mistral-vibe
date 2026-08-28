from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
import respx

from tests.app_server.backend_contract.conftest import BackendContractConnection
from tests.constants import CONNECTORS_BOOTSTRAP_PATH, MISTRAL_BASE_URL
from vibe.app_server._legacy_session_backend import LegacySessionBackend
from vibe.app_server._session_backend_port import SessionBackendError
from vibe.app_server.client import AppServerClient
import vibe.app_server.connector_catalog as connector_catalog_module
from vibe.app_server.models import MCPSourceKind
from vibe.app_server.protocol import (
    AppServerResponseError,
    ConnectorAuthReadParams,
    ConnectorAuthReadResponse,
    ConnectorCatalogAuthRequestParams,
    ConnectorCatalogAuthRequestResponse,
    ConnectorCatalogMutationResponse,
    ConnectorCatalogReadParams,
    ConnectorCatalogReadResponse,
    ConnectorCatalogRefreshParams,
    ConnectorCatalogToggleParams,
    ConnectorRefreshParams,
    ConnectorRefreshResponse,
    ConnectorsReadParams,
    ConnectorsReadResponse,
    ProtocolErrorCode,
    SessionStartParams,
    SessionStartResponse,
    SessionStopParams,
)
from vibe.app_server.session import AppServerSession
from vibe.core.config import VibeConfigSchema
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.types import ConcurrencyConflictError


def _connector(
    connector_id: str,
    name: str,
    *,
    ready: bool = True,
    auth_action: str = "none",
    tools: tuple[str, ...] = ("search",),
) -> dict[str, object]:
    return {
        "id": connector_id,
        "name": name,
        "status": {"is_ready": ready},
        **({"auth_action": {"type": auth_action}} if auth_action != "none" else {}),
        "tools": [
            {
                "name": tool,
                "description": f"{tool} through {name}",
                "inputSchema": {"type": "object", "properties": {}},
            }
            for tool in tools
        ],
    }


def _payload(*connectors: dict[str, object]) -> dict[str, object]:
    return {"connectors": list(connectors)}


async def _open_connector_session(
    connection: BackendContractConnection,
    respx_mock: respx.MockRouter,
    payload: dict[str, object],
) -> tuple[AppServerSession, respx.Route]:
    bootstrap_route = respx_mock.get(f"{MISTRAL_BASE_URL}{CONNECTORS_BOOTSTRAP_PATH}")
    bootstrap_route.mock(return_value=httpx.Response(200, json=payload))
    return await connection.host.open_session(), bootstrap_route


def _source(response: ConnectorCatalogReadResponse, alias: str):
    assert response.session is not None
    return next(source for source in response.session.sources if source.alias == alias)


async def _assert_projection_agreement(
    session: AppServerSession,
    response: ConnectorCatalogReadResponse,
    *,
    catalog_is_accepted: bool = True,
) -> None:
    assert response.session is not None
    await session.resources.runtime.refresh()
    runtime = session.resources.runtime
    accepted = response.session
    connector_sources = [
        source for source in runtime.mcp.sources if source.kind.value == "connector"
    ]

    assert runtime.connectors.total == len(accepted.sources)
    assert runtime.connectors.connected == sum(
        source.status == "connected" for source in accepted.sources
    )
    assert [source.name for source in connector_sources] == [
        source.alias for source in accepted.sources
    ]
    for projected, source in zip(connector_sources, accepted.sources, strict=True):
        assert projected.status.value == source.status
        assert [(tool.name, tool.enabled) for tool in projected.tools] == [
            (tool.name, tool.enabled) for tool in source.tools
        ]
    if catalog_is_accepted:
        assert accepted.accepted_catalog_revision == response.catalog.catalog_revision
    assert accepted.accepted_selection_revision
    assert accepted.route_revision


def _backend_class(experimental_harness: bool) -> type[Any]:
    if not experimental_harness:
        return LegacySessionBackend
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
    )

    return UnifiedHarnessBackendAdapter


def _record_incoming(
    client: AppServerClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[dict[str, Any]], asyncio.Event, Callable[[dict[str, Any]], Any]]:
    messages: list[dict[str, Any]] = []
    changed = asyncio.Event()
    dispatch = client._dispatch

    async def record(message: dict[str, Any]) -> None:
        messages.append(message.copy())
        changed.set()
        await dispatch(message)

    monkeypatch.setattr(client, "_dispatch", record)
    return messages, changed, dispatch


async def _wait_for_message(
    messages: list[dict[str, Any]],
    changed: asyncio.Event,
    predicate: Callable[[dict[str, Any]], bool],
) -> None:
    async def wait() -> None:
        while not any(predicate(message) for message in messages):
            changed.clear()
            if any(predicate(message) for message in messages):
                return
            await changed.wait()

    await asyncio.wait_for(wait(), timeout=2)


@pytest.mark.asyncio
async def test_connector_catalog_default_selection_matches_production_composition(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api,
    respx_mock: respx.MockRouter,
    experimental_harness: bool,
) -> None:
    """*Prepare*: A ready and an authorization-actionable connector have no explicit config.
    *Do*: Start the selected production composition and read its canonical catalog.
    *Assert*: Legacy disables absence while Unified enables ready allowed connectors in memory.
    """
    session, _ = await _open_connector_session(
        backend_contract_connection,
        respx_mock,
        _payload(
            _connector("github/raw", "github", tools=("search", "write")),
            _connector("oauth/raw", "oauth", ready=False, auth_action="oauth"),
        ),
    )
    try:
        response = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )

        expected_ready = "connected" if experimental_harness else "disabled"
        expected_oauth = "needs_auth" if experimental_harness else "disabled"
        assert response.catalog.catalog_revision
        assert response.catalog.disposition == "memory"
        assert _source(response, "github").status == expected_ready
        assert _source(response, "oauth").status == expected_oauth
        assert response.session is not None
        assert response.session.accepted_catalog_revision == (
            response.catalog.catalog_revision
        )
        compatibility = ConnectorsReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connectors/read", ConnectorsReadParams(session_id=session.session_id)
            )
        )
        assert compatibility.counts.total == 2
        assert compatibility.counts.connected == int(experimental_harness)
        await _assert_projection_agreement(session, response)
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_invalid_initial_connector_bootstrap_keeps_session_usable(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api,
    respx_mock: respx.MockRouter,
) -> None:
    """An invalid initial catalog is unavailable instead of aborting session startup."""
    duplicate_tools = _connector("github/raw", "github", tools=("search", " search "))
    session, _ = await _open_connector_session(
        backend_contract_connection, respx_mock, _payload(duplicate_tools)
    )
    try:
        response = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )

        assert response.catalog.disposition == "not_loaded"
        assert response.catalog.catalog_revision is None
        assert response.session is not None
        assert response.session.sources == []
        assert session.resources.runtime.connectors.total == 0
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_public_mcp_resource_combines_connector_sources(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api,
    respx_mock: respx.MockRouter,
    experimental_harness: bool,
) -> None:
    """*Prepare*: The selected backend has one accepted connector source.
    *Do*: Read it through the shared MCP client resource.
    *Assert*: The backend-independent combined view contains the accepted connector.
    """
    session, _ = await _open_connector_session(
        backend_contract_connection,
        respx_mock,
        _payload(_connector("github/raw", "github")),
    )
    try:
        state = await session.resources.mcp.read()
        connector = next(
            source for source in state.sources if source.kind is MCPSourceKind.CONNECTOR
        )
        assert connector.name == "github"
        assert connector.status.value == (
            "connected" if experimental_harness else "disabled"
        )
        assert [(tool.name, tool.enabled) for tool in connector.tools] == [
            ("search", experimental_harness)
        ]
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_connector_catalog_explicit_toggle_overrides_each_process_default(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api,
    respx_mock: respx.MockRouter,
    experimental_harness: bool,
) -> None:
    """*Prepare*: One ready connector starts with the backend-specific absent-config default.
    *Do*: Persist the opposite explicit source selection against the accepted session.
    *Assert*: Both backends converge to the explicit value and return matching revisions.
    """
    session, _ = await _open_connector_session(
        backend_contract_connection,
        respx_mock,
        _payload(_connector("github/raw", "github")),
    )
    try:
        disabled = experimental_harness
        mutation = ConnectorCatalogMutationResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/toggle",
                ConnectorCatalogToggleParams(
                    session_id=session.session_id, alias="github", disabled=disabled
                ),
            )
        )
        response = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )

        assert mutation.runtime is not None
        assert mutation.catalog_revision == mutation.accepted_catalog_revision
        assert mutation.selection_revision == mutation.accepted_selection_revision
        assert response.session is not None
        assert mutation.accepted_selection_revision == (
            response.session.accepted_selection_revision
        )
        assert mutation.route_revision == response.session.route_revision
        assert _source(response, "github").status == (
            "disabled" if disabled else "connected"
        )
        selection = next(item for item in response.selections if item.alias == "github")
        assert selection.disabled is disabled
        assert selection.state == "resolved"
        await _assert_projection_agreement(session, response)
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_sessionless_connector_toggle_is_pending_and_does_not_bootstrap(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api,
    respx_mock: respx.MockRouter,
) -> None:
    """*Prepare*: No session or catalog has been created.
    *Do*: Persist a normalized connector alias selection without a target.
    *Assert*: The write is pending, creates no Runtime, and performs no bootstrap request.
    """
    mutation = ConnectorCatalogMutationResponse.model_validate(
        await backend_contract_connection.client.request(
            "connector_catalog/toggle",
            ConnectorCatalogToggleParams(alias="future_connector", disabled=False),
        )
    )

    assert mutation.runtime is None
    assert mutation.pending_selection is True
    bootstrap_route = respx_mock.get(f"{MISTRAL_BASE_URL}{CONNECTORS_BOOTSTRAP_PATH}")
    assert bootstrap_route.call_count == 0


@pytest.mark.asyncio
async def test_sessionless_ineffective_toggle_succeeds_with_live_session(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api,
    respx_mock: respx.MockRouter,
) -> None:
    """A pending alias does not affect the live accepted selection and may persist."""
    session, bootstrap_route = await _open_connector_session(
        backend_contract_connection,
        respx_mock,
        _payload(_connector("github/raw", "github")),
    )
    try:
        before = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )
        mutation = ConnectorCatalogMutationResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/toggle",
                ConnectorCatalogToggleParams(alias="future_connector", disabled=False),
            )
        )
        after = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )

        assert mutation.runtime is None
        assert mutation.pending_selection is True
        assert before.session is not None
        assert after.session is not None
        assert after.session.accepted_catalog_revision == (
            before.session.accepted_catalog_revision
        )
        assert after.session.accepted_selection_revision == (
            before.session.accepted_selection_revision
        )
        assert after.session.route_revision == before.session.route_revision
        assert any(
            selection.alias == "future_connector" and selection.state == "pending"
            for selection in after.selections
        )
        assert bootstrap_route.call_count == 1
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_sessionless_effective_toggle_conflicts_before_persistence(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api,
    respx_mock: respx.MockRouter,
    experimental_harness: bool,
) -> None:
    """A mutation that changes the live effective selection requires a target."""
    session, bootstrap_route = await _open_connector_session(
        backend_contract_connection,
        respx_mock,
        _payload(_connector("github/raw", "github")),
    )
    try:
        before = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )
        with pytest.raises(AppServerResponseError) as exc_info:
            await backend_contract_connection.client.request(
                "connector_catalog/toggle",
                ConnectorCatalogToggleParams(
                    alias="github", disabled=experimental_harness
                ),
            )
        after = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )

        assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
        assert all(selection.alias != "github" for selection in after.selections)
        assert before.session is not None
        assert after.session is not None
        assert after.session.accepted_selection_revision == (
            before.session.accepted_selection_revision
        )
        assert after.session.route_revision == before.session.route_revision
        assert bootstrap_route.call_count == 1
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_targeted_unknown_connector_alias_is_rejected_without_persistence(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api,
    respx_mock: respx.MockRouter,
) -> None:
    """*Prepare*: A live session accepted a catalog that does not contain the requested alias.
    *Do*: Target a connector toggle at that session.
    *Assert*: The facade returns NOT_FOUND before writing a pending selection.
    """
    session, _ = await _open_connector_session(
        backend_contract_connection,
        respx_mock,
        _payload(_connector("github/raw", "github")),
    )
    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await backend_contract_connection.client.request(
                "connector_catalog/toggle",
                ConnectorCatalogToggleParams(
                    session_id=session.session_id, alias="missing", disabled=False
                ),
            )

        assert exc_info.value.error.code is ProtocolErrorCode.NOT_FOUND
        response = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )
        assert all(item.alias != "missing" for item in response.selections)
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_connector_refresh_is_full_catalog_and_compatibility_adapter_matches(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api,
    respx_mock: respx.MockRouter,
) -> None:
    """*Prepare*: A session accepted one connector and bootstrap later returns a replacement catalog.
    *Do*: Force canonical refresh, then use the per-name compatibility refresh adapter.
    *Assert*: Both operations fetch the complete account catalog and project the same live Runtime.
    """
    session, bootstrap_route = await _open_connector_session(
        backend_contract_connection,
        respx_mock,
        _payload(_connector("github/raw", "github")),
    )
    try:
        replacement = _payload(
            _connector("github/raw", "github", tools=("search", "write")),
            _connector("linear/raw", "linear"),
        )
        bootstrap_route.mock(return_value=httpx.Response(200, json=replacement))
        canonical = ConnectorCatalogMutationResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/refresh",
                ConnectorCatalogRefreshParams(session_id=session.session_id),
            )
        )
        compatibility = ConnectorRefreshResponse.model_validate(
            await backend_contract_connection.client.request(
                "connectors/refresh",
                ConnectorRefreshParams(session_id=session.session_id, name="github"),
            )
        )
        response = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )
        await _assert_projection_agreement(session, response)

        assert canonical.runtime is not None
        assert canonical.catalog_revision == canonical.accepted_catalog_revision
        assert compatibility.tool_count in {0, 2}
        assert compatibility.runtime.connectors.total == 2
        assert session.resources.runtime.connectors.total == 2
        assert bootstrap_route.call_count == 3
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_failed_forced_refresh_retains_the_accepted_session_routes(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api,
    respx_mock: respx.MockRouter,
) -> None:
    """*Prepare*: A target accepted an explicitly enabled ready connector.
    *Do*: Its next forced full bootstrap fails.
    *Assert*: The prior catalog revision, route revision, and callable source remain accepted.
    """
    session, bootstrap_route = await _open_connector_session(
        backend_contract_connection,
        respx_mock,
        _payload(_connector("github/raw", "github")),
    )
    try:
        await backend_contract_connection.client.request(
            "connector_catalog/toggle",
            ConnectorCatalogToggleParams(
                session_id=session.session_id, alias="github", disabled=False
            ),
        )
        before = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )
        bootstrap_route.mock(return_value=httpx.Response(503, text="private body"))

        with pytest.raises(AppServerResponseError) as exc_info:
            await backend_contract_connection.client.request(
                "connector_catalog/refresh",
                ConnectorCatalogRefreshParams(session_id=session.session_id),
            )

        after = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )
        assert exc_info.value.error.code is ProtocolErrorCode.INTERNAL_ERROR
        assert "private body" not in exc_info.value.error.message
        assert after.catalog.catalog_revision == before.catalog.catalog_revision
        assert after.session is not None
        assert before.session is not None
        assert after.session.accepted_catalog_revision == (
            before.session.accepted_catalog_revision
        )
        assert after.session.route_revision == before.session.route_revision
        assert _source(after, "github").status == "connected"
        await _assert_projection_agreement(session, after)
    finally:
        await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["restrictive", "additive"])
async def test_connector_toggle_persistence_and_convergence_ordering(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api,
    respx_mock: respx.MockRouter,
    experimental_harness: bool,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    """*Prepare*: A target has a known connector and a stable accepted selection.
    *Do*: A restrictive persistence write fails, or additive acceptance fails.
    *Assert*: Persistence preflight/write precedes convergence and no route is pre-suspended.
    """
    session, _ = await _open_connector_session(
        backend_contract_connection,
        respx_mock,
        _payload(_connector("github/raw", "github")),
    )
    try:
        initial_disabled = change == "additive"
        await backend_contract_connection.client.request(
            "connector_catalog/toggle",
            ConnectorCatalogToggleParams(
                session_id=session.session_id, alias="github", disabled=initial_disabled
            ),
        )
        backend_type = _backend_class(experimental_harness)
        events: list[str] = []
        original_persist = connector_catalog_module.persist_mcp_toggle
        original_reconfigure = backend_type.reconfigure_connectors
        original_suspend = backend_type.suspend_connectors

        async def persist(
            orchestrator: ConfigOrchestrator[VibeConfigSchema],
            *,
            name: str,
            is_connector: bool,
            disabled: bool,
            tool_name: str | None = None,
            preflight: Callable[[VibeConfigSchema], Awaitable[None]] | None = None,
        ) -> None:
            events.append("persist")
            if change == "restrictive":
                raise ConcurrencyConflictError("expected", "actual")
            await original_persist(
                orchestrator,
                name=name,
                is_connector=is_connector,
                disabled=disabled,
                tool_name=tool_name,
                preflight=preflight,
            )

        async def reconfigure(backend, *args: object, **kwargs: object):
            events.append("reconfigure")
            if change == "additive":
                raise SessionBackendError(
                    ProtocolErrorCode.INTERNAL_ERROR,
                    "injected connector acceptance failure",
                )
            return await original_reconfigure(backend, *args, **kwargs)

        async def suspend(backend, *args: object, **kwargs: object):
            events.append("suspend")
            return await original_suspend(backend, *args, **kwargs)

        monkeypatch.setattr(connector_catalog_module, "persist_mcp_toggle", persist)
        monkeypatch.setattr(backend_type, "reconfigure_connectors", reconfigure)
        monkeypatch.setattr(backend_type, "suspend_connectors", suspend)

        with pytest.raises(AppServerResponseError) as exc_info:
            await backend_contract_connection.client.request(
                "connector_catalog/toggle",
                ConnectorCatalogToggleParams(
                    session_id=session.session_id,
                    alias="github",
                    disabled=change == "restrictive",
                ),
            )

        response = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )
        selection = next(item for item in response.selections if item.alias == "github")
        if change == "restrictive":
            assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
            assert events == ["persist"]
            assert selection.disabled is False
            assert _source(response, "github").status == "connected"
        else:
            assert exc_info.value.error.code is ProtocolErrorCode.INTERNAL_ERROR
            assert events == ["persist", "reconfigure"]
            assert selection.disabled is False
            assert _source(response, "github").status == "disabled"
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_terminal_connector_rejection_is_not_retried_until_revision_changes(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api,
    respx_mock: respx.MockRouter,
    experimental_harness: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deterministic rejection is terminal for its catalog/selection pair."""
    session, _ = await _open_connector_session(
        backend_contract_connection,
        respx_mock,
        _payload(_connector("github/raw", "github")),
    )
    try:
        await backend_contract_connection.client.request(
            "connector_catalog/toggle",
            ConnectorCatalogToggleParams(
                session_id=session.session_id, alias="github", disabled=True
            ),
        )
        backend_type = _backend_class(experimental_harness)
        original_reconfigure = backend_type.reconfigure_connectors
        attempted_revisions: list[tuple[str, str]] = []

        async def reject_once(backend, catalog, selection, *, force: bool):
            attempted_revisions.append((catalog.revision, selection.selection_revision))
            if len(attempted_revisions) == 1:
                raise SessionBackendError(
                    ProtocolErrorCode.INTERNAL_ERROR,
                    "injected deterministic connector rejection",
                )
            return await original_reconfigure(backend, catalog, selection, force=force)

        monkeypatch.setattr(backend_type, "reconfigure_connectors", reject_once)

        for _ in range(2):
            with pytest.raises(AppServerResponseError) as exc_info:
                await backend_contract_connection.client.request(
                    "connector_catalog/toggle",
                    ConnectorCatalogToggleParams(
                        session_id=session.session_id, alias="github", disabled=False
                    ),
                )
            assert exc_info.value.error.code is ProtocolErrorCode.INTERNAL_ERROR

        assert len(attempted_revisions) == 1
        recovered = ConnectorCatalogMutationResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/toggle",
                ConnectorCatalogToggleParams(
                    session_id=session.session_id, alias="github", disabled=True
                ),
            )
        )

        assert len(attempted_revisions) == 2
        assert attempted_revisions[1][0] == attempted_revisions[0][0]
        assert attempted_revisions[1][1] != attempted_revisions[0][1]
        assert recovered.selection_revision == recovered.accepted_selection_revision
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_connector_refresh_retains_old_session_revision_while_busy(
    backend_contract_connection: BackendContractConnection,
    backend_contract_gated_mistral_response,
    backend_contract_mistral_api: respx.Route,
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: A target is executing while bootstrap has a new complete catalog.
    *Do*: Force targeted convergence during the active turn.
    *Assert*: The host retains the exact candidate and accepts it automatically when idle.
    """
    session, bootstrap_route = await _open_connector_session(
        backend_contract_connection,
        respx_mock,
        _payload(_connector("github/raw", "github")),
    )
    started = asyncio.Event()
    release = asyncio.Event()
    backend_contract_mistral_api.mock(
        return_value=backend_contract_gated_mistral_response(
            "finished", started=started, release=release
        )
    )

    async def consume_turn() -> None:
        _ = [event async for event in session.act("wait")]

    turn = asyncio.create_task(consume_turn())
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        before = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )
        bootstrap_route.mock(
            return_value=httpx.Response(
                200,
                json=_payload(
                    _connector("github/raw", "github"),
                    _connector("linear/raw", "linear"),
                ),
            )
        )
        with pytest.raises(AppServerResponseError) as exc_info:
            await backend_contract_connection.client.request(
                "connector_catalog/refresh",
                ConnectorCatalogRefreshParams(session_id=session.session_id),
            )
        pending = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )

        assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
        assert before.session is not None
        assert pending.session is not None
        assert pending.catalog.catalog_revision != before.catalog.catalog_revision
        assert pending.session.accepted_catalog_revision == (
            before.session.accepted_catalog_revision
        )
        assert pending.session.accepted_selection_revision == (
            before.session.accepted_selection_revision
        )
        assert pending.session.route_revision == before.session.route_revision
        assert [source.alias for source in pending.session.sources] == ["github"]
        await _assert_projection_agreement(session, pending, catalog_is_accepted=False)

        messages, changed, _ = _record_incoming(
            backend_contract_connection.client, monkeypatch
        )
        release.set()
        await turn
        await _wait_for_message(
            messages,
            changed,
            lambda message: message.get("method") == "runtime/updated",
        )
        accepted = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )
        assert accepted.session is not None
        assert accepted.session.accepted_catalog_revision == (
            accepted.catalog.catalog_revision
        )
        assert accepted.session.accepted_catalog_revision == (
            pending.catalog.catalog_revision
        )
        assert accepted.session.accepted_selection_revision
        assert [source.alias for source in accepted.session.sources] == [
            "github",
            "linear",
        ]
        assert bootstrap_route.call_count == 2
        await _assert_projection_agreement(session, accepted)
    finally:
        release.set()
        if not turn.done():
            await turn
        await session.close()


@pytest.mark.asyncio
async def test_busy_targeted_toggle_persists_and_converges_retained_candidate(
    backend_contract_connection: BackendContractConnection,
    backend_contract_gated_mistral_response,
    backend_contract_mistral_api: respx.Route,
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Busy convergence retains the post-write candidate without rereading config."""
    session, bootstrap_route = await _open_connector_session(
        backend_contract_connection,
        respx_mock,
        _payload(_connector("github/raw", "github")),
    )
    await backend_contract_connection.client.request(
        "connector_catalog/toggle",
        ConnectorCatalogToggleParams(
            session_id=session.session_id, alias="github", disabled=False
        ),
    )
    started = asyncio.Event()
    release = asyncio.Event()
    backend_contract_mistral_api.mock(
        return_value=backend_contract_gated_mistral_response(
            "finished", started=started, release=release
        )
    )

    async def consume_turn() -> None:
        _ = [event async for event in session.act("wait")]

    turn = asyncio.create_task(consume_turn())
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        before = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )
        with pytest.raises(AppServerResponseError) as exc_info:
            await backend_contract_connection.client.request(
                "connector_catalog/toggle",
                ConnectorCatalogToggleParams(
                    session_id=session.session_id, alias="github", disabled=True
                ),
            )
        pending = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )

        assert exc_info.value.error.code is ProtocolErrorCode.CONFLICT
        assert before.session is not None
        assert pending.session is not None
        selection = next(item for item in pending.selections if item.alias == "github")
        assert selection.disabled is True
        assert pending.session.accepted_selection_revision == (
            before.session.accepted_selection_revision
        )
        assert pending.session.route_revision == before.session.route_revision
        assert _source(pending, "github").status == "connected"

        def reject_config_reread(*_args: object, **_kwargs: object):
            raise AssertionError("pending convergence reread connector configuration")

        monkeypatch.setattr(
            connector_catalog_module,
            "resolve_connector_selection",
            reject_config_reread,
        )
        messages, changed, _ = _record_incoming(
            backend_contract_connection.client, monkeypatch
        )
        release.set()
        await turn
        await _wait_for_message(
            messages,
            changed,
            lambda message: message.get("method") == "runtime/updated",
        )
        accepted = ConnectorCatalogReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/read",
                ConnectorCatalogReadParams(session_id=session.session_id),
            )
        )

        assert accepted.session is not None
        assert accepted.session.accepted_catalog_revision == (
            pending.session.accepted_catalog_revision
        )
        assert accepted.session.accepted_selection_revision != (
            pending.session.accepted_selection_revision
        )
        assert _source(accepted, "github").status == "disabled"
        assert bootstrap_route.call_count == 1
        await _assert_projection_agreement(session, accepted)
    finally:
        release.set()
        if not turn.done():
            await turn
        await session.close()


@pytest.mark.asyncio
async def test_connector_authorization_acknowledges_before_notifications_and_compatibility_adapts(
    backend_contract_connection: BackendContractConnection,
    backend_contract_mistral_api,
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: An accepted enabled connector requires OAuth and URL lookup succeeds.
    *Do*: Request canonical authorization, then use the synchronous compatibility adapter.
    *Assert*: Canonical response precedes required/URL notifications; compatibility returns only its URL.
    """
    bootstrap_route = respx_mock.get(f"{MISTRAL_BASE_URL}{CONNECTORS_BOOTSTRAP_PATH}")
    bootstrap_route.mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                _connector("oauth/raw", "oauth", ready=False, auth_action="oauth")
            ),
        )
    )

    async def auth_url(*_args: object, **_kwargs: object) -> str:
        return "https://auth.example.test/authorize"

    monkeypatch.setattr(connector_catalog_module, "_connector_auth_url", auth_url)
    session_id: str | None = None
    original_dispatch: Callable[[dict[str, Any]], Any] | None = None
    try:
        started = SessionStartResponse.model_validate(
            await backend_contract_connection.client.request(
                "session/start", SessionStartParams()
            )
        )
        session_id = started.state.session.id
        await backend_contract_connection.client.request(
            "connector_catalog/toggle",
            ConnectorCatalogToggleParams(
                session_id=session_id, alias="oauth", disabled=False
            ),
        )
        messages, changed, original_dispatch = _record_incoming(
            backend_contract_connection.client, monkeypatch
        )
        request_id = f"client-{backend_contract_connection.client._next_request_id}"
        response = ConnectorCatalogAuthRequestResponse.model_validate(
            await backend_contract_connection.client.request(
                "connector_catalog/auth/request",
                ConnectorCatalogAuthRequestParams(session_id=session_id, alias="oauth"),
            )
        )
        await _wait_for_message(
            messages,
            changed,
            lambda message: (
                message.get("method") == "connector_catalog/authUrl"
                and message.get("params", {}).get("requestId") == response.request_id
            ),
        )

        response_index = next(
            index
            for index, message in enumerate(messages)
            if message.get("id") == request_id
        )
        assert any(
            message.get("method") == "connector_catalog/authRequired"
            for message in messages
        ), messages
        required_index = next(
            index
            for index, message in enumerate(messages)
            if message.get("method") == "connector_catalog/authRequired"
        )
        url_index = next(
            index
            for index, message in enumerate(messages)
            if message.get("method") == "connector_catalog/authUrl"
        )
        assert response.session_id == session_id
        assert response.alias == "oauth"
        assert response_index < required_index < url_index

        compatibility_start = len(messages)
        compatibility = ConnectorAuthReadResponse.model_validate(
            await backend_contract_connection.client.request(
                "connectors/auth/read",
                ConnectorAuthReadParams(session_id=session_id, name="oauth"),
            )
        )
        await asyncio.sleep(0)
        assert compatibility.url == "https://auth.example.test/authorize"
        assert all(
            message.get("method")
            not in {"connector_catalog/authRequired", "connector_catalog/authUrl"}
            for message in messages[compatibility_start:]
        )
    finally:
        if original_dispatch is not None:
            monkeypatch.setattr(
                backend_contract_connection.client, "_dispatch", original_dispatch
            )
        if session_id is not None:
            try:
                await backend_contract_connection.client.request(
                    "session/stop", SessionStopParams(session_id=session_id)
                )
            except (AppServerResponseError, RuntimeError):
                pass
