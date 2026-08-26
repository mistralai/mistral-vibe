from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

import pytest

import vibe.app_server._integration_resources as integration_resources
from vibe.app_server._integration_resources import MCPResource
from vibe.app_server.models import MCPState
from vibe.app_server.protocol import (
    AppServerResponseError,
    ConnectorCatalogMutationResponse,
    ConnectorCatalogReadResponse,
    ConnectorCatalogRefreshParams,
    ConnectorCatalogToggleParams,
    ConnectorCatalogView,
    MCPReadResponse,
    ProtocolError,
    ProtocolErrorCode,
    ProtocolModel,
)


class FakeClient:
    def __init__(self) -> None:
        self.requests: list[tuple[str, ProtocolModel]] = []

    async def request(self, method: str, params: ProtocolModel) -> dict[str, Any]:
        self.requests.append((method, params))
        return ConnectorCatalogMutationResponse().model_dump(mode="json", by_alias=True)


class RecoveringReadClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self._connector_reads = 0

    async def request(self, method: str, params: ProtocolModel) -> dict[str, Any]:
        self.requests.append((method, params))
        if method == "mcp_catalog/read":
            return MCPReadResponse(mcp=MCPState()).model_dump(
                mode="json", by_alias=True
            )
        if method != "connector_catalog/read":
            raise AssertionError(f"Unexpected method: {method}")
        self._connector_reads += 1
        if self._connector_reads == 1:
            raise AppServerResponseError(
                ProtocolError(
                    code=ProtocolErrorCode.INTERNAL_ERROR,
                    message="Connector catalog unavailable",
                )
            )
        return ConnectorCatalogReadResponse(
            catalog=ConnectorCatalogView(disposition="memory")
        ).model_dump(mode="json", by_alias=True)


@dataclass
class FakeConnection:
    client: FakeClient

    async def connect(self) -> FakeClient:
        return self.client


@dataclass
class FakeState:
    session_id: str = "session-1"
    mcp: MCPState = field(default_factory=MCPState)
    applied: list[object] = field(default_factory=list)

    def apply_runtime(self, runtime: object) -> None:
        self.applied.append(runtime)
        self.mcp = cast(Any, runtime).mcp


@pytest.mark.asyncio
async def test_mcp_resource_routes_connector_actions_to_connector_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    state = FakeState()
    runtime = SimpleNamespace(mcp=MCPState())
    monkeypatch.setattr(
        integration_resources,
        "_required_mcp_runtime",
        lambda _runtime: cast(Any, runtime),
    )
    resource = MCPResource(cast(Any, FakeConnection(client)), cast(Any, state))

    await resource.refresh_connectors()
    await resource.toggle(
        "github", source="connector", disabled=True, tool_name="search"
    )

    assert client.requests == [
        (
            "connector_catalog/refresh",
            ConnectorCatalogRefreshParams(session_id="session-1"),
        ),
        (
            "connector_catalog/toggle",
            ConnectorCatalogToggleParams(
                session_id="session-1",
                alias="github",
                disabled=True,
                tool_name="search",
            ),
        ),
    ]
    assert state.applied == [runtime]


@pytest.mark.asyncio
async def test_mcp_resource_clears_connector_error_after_successful_read() -> None:
    client = RecoveringReadClient()
    state = FakeState()
    resource = MCPResource(cast(Any, FakeConnection(client)), cast(Any, state))

    failed = await resource.read()
    recovered = await resource.read()

    assert failed.connector_error == "Connector catalog unavailable"
    assert recovered.connector_error is None
