from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

import pytest

import vibe.app_server._integration_resources as integration_resources
from vibe.app_server._integration_resources import MCPResource, PluginCatalogResource
from vibe.app_server.models import MCPState, PluginCatalogEntry, PluginCatalogState
from vibe.app_server.protocol import (
    AppServerResponseError,
    ConnectorCatalogMutationResponse,
    ConnectorCatalogReadResponse,
    ConnectorCatalogRefreshParams,
    ConnectorCatalogToggleParams,
    ConnectorCatalogView,
    MCPReadResponse,
    PluginCatalogReadResponse,
    PluginReloadResponse,
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


def _catalog(**pins: str) -> PluginCatalogState:
    return PluginCatalogState(
        plugins=[
            PluginCatalogEntry(
                name=name,
                source_format="agent_plugins_1_0",
                manifest_digest=f"manifest-{name}",
                content_sha256=digest,
            )
            for name, digest in pins.items()
        ]
    )


class PluginClient(FakeClient):
    """Answer each ``plugin_catalog/read`` from the next catalogue in line."""

    def __init__(self, *catalogs: PluginCatalogState) -> None:
        super().__init__()
        self._catalogs = list(catalogs)

    async def request(self, method: str, params: ProtocolModel) -> dict[str, Any]:
        self.requests.append((method, params))
        if method == "plugin/reload":
            return PluginReloadResponse().model_dump(mode="json", by_alias=True)
        if method != "plugin_catalog/read":
            raise AssertionError(f"Unexpected method: {method}")
        return PluginCatalogReadResponse(plugins=self._catalogs.pop(0)).model_dump(
            mode="json", by_alias=True
        )


class PluginlessClient(FakeClient):
    def __init__(self, code: ProtocolErrorCode) -> None:
        super().__init__()
        self._code = code

    async def request(self, method: str, params: ProtocolModel) -> dict[str, Any]:
        self.requests.append((method, params))
        raise AppServerResponseError(
            ProtocolError(code=self._code, message="No plugins here")
        )


def _plugin_resource(client: FakeClient) -> PluginCatalogResource:
    return PluginCatalogResource(
        cast(Any, FakeConnection(client)), cast(Any, FakeState())
    )


@pytest.mark.asyncio
async def test_plugin_reload_reports_what_moved_by_digest() -> None:
    resource = _plugin_resource(
        PluginClient(
            _catalog(productivity="0bbb23a0", frontend="1e040938"),
            _catalog(productivity="4c71ea55", lean="aa10bb20"),
        )
    )

    diff = await resource.reload()

    assert diff is not None
    assert [(change.name, change.before, change.after) for change in diff.changes] == [
        ("frontend", "1e040938", None),
        ("lean", None, "aa10bb20"),
        ("productivity", "0bbb23a0", "4c71ea55"),
    ]
    # The after-image rides along, so rendering the result costs no third read.
    assert [entry.name for entry in diff.state.plugins] == ["productivity", "lean"]


@pytest.mark.asyncio
async def test_plugin_reload_that_moves_nothing_reports_no_changes() -> None:
    pinned = _catalog(productivity="0bbb23a0")
    resource = _plugin_resource(PluginClient(pinned, pinned))

    diff = await resource.reload()

    assert diff is not None and diff.changes == ()


@pytest.mark.asyncio
async def test_plugin_reload_re_pins_between_the_two_reads() -> None:
    pinned = _catalog(productivity="0bbb23a0")
    client = PluginClient(pinned, pinned)

    await _plugin_resource(client).reload()

    assert [method for method, _params in client.requests] == [
        "plugin_catalog/read",
        "plugin/reload",
        "plugin_catalog/read",
    ]


@pytest.mark.parametrize(
    "code", [ProtocolErrorCode.NOT_IMPLEMENTED, ProtocolErrorCode.METHOD_NOT_FOUND]
)
@pytest.mark.asyncio
async def test_a_backend_that_resolves_no_plugins_leaves_the_commands_unavailable(
    code: ProtocolErrorCode,
) -> None:
    # A build without the service answers method-not-found; a session backend
    # that resolves nothing answers not-implemented. Neither has plugins.
    resource = _plugin_resource(PluginlessClient(code))

    assert await resource.read() is None
    assert await resource.reload() is None
    assert resource.supported is False


@pytest.mark.asyncio
async def test_a_read_that_fails_for_another_reason_is_not_mistaken_for_no_plugins() -> (
    None
):
    resource = _plugin_resource(PluginlessClient(ProtocolErrorCode.INTERNAL_ERROR))

    with pytest.raises(AppServerResponseError):
        await resource.read()


@pytest.mark.asyncio
async def test_a_read_that_answers_marks_the_catalogue_supported() -> None:
    resource = _plugin_resource(PluginClient(_catalog(productivity="0bbb23a0")))

    assert resource.supported is False
    assert await resource.read() is not None
    assert resource.supported is True
