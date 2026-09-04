"""Plugin-owned MCP servers: their two names, their login, and what /mcp shows."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._mcp_auth import MCPAuthenticationService
from vibe.app_server._plugin_mcp import PluginMCPCatalog
from vibe.app_server._runtime import build_unified_runtime_snapshot
from vibe.app_server._session_backend_port import SessionMCPState
from vibe.app_server.mcp_catalog import MCPCatalogService
from vibe.app_server.models import MCPSourceKind, MCPSourceStatus, MCPState
from vibe.app_server.protocol import (
    MCPCatalogMutationResponse,
    MCPLoginParams,
    MCPLogoutParams,
    MCPReadParams,
    MCPReadResponse,
    MCPRefreshParams,
    MCPRemoveParams,
    MCPToggleParams,
    ProtocolErrorCode,
    RuntimeUpdatedParams,
)
from vibe.core.agents.manager import AgentManager
from vibe.core.auth.mcp_oauth import Fingerprint
from vibe.core.config import MCPHttp, MCPOAuth, MCPStaticAuth
from vibe.core.plugins import PluginMCPAuthorizationRequired, PluginMCPServerDefinition
from vibe.core.tools.mcp.registry import MCPRegistry
from vibe.core.tools.mcp.tools import RemoteTool

if TYPE_CHECKING:
    from collections.abc import Sequence

    from vibe.app_server._model import ProtocolModel
    from vibe.app_server._session_backend_port import (
        MCPAuthorizationRef,
        ResolvedMCPCatalog,
    )
    from vibe.core.config import MCPServer

_ALIAS = "plugin_2f8a1c_figma"
_RIVAL_ALIAS = "plugin_7c31de_figma"
# What ``resolve_tool_group_names`` derives for the two plugins below once both
# declare ``figma``. Pinned rather than recomputed: it is the name /mcp shows,
# the keyring keys on and a login is typed against, so a change here is a
# change users see.
_CONTESTED = "figma_a6729baf"
_RIVAL_CONTESTED = "figma_47646b90"


def _server() -> MCPHttp:
    return MCPHttp(
        name=_ALIAS,
        transport="http",
        url="https://mcp.figma.test/mcp",
        auth=MCPOAuth(type="oauth", scopes=["read"]),
    )


def _declared_server() -> MCPHttp:
    return MCPHttp(
        name=_ALIAS,
        transport="http",
        url="https://mcp.figma.test/mcp",
        auth=MCPStaticAuth(headers={"X-Figma-Plugin-Bundle": "figma_prod@2_2_96"}),
    )


def _configured_server() -> MCPHttp:
    return MCPHttp(
        name="figma",
        transport="http",
        url="https://figma.example/mcp",
        auth=MCPOAuth(type="oauth", scopes=[]),
    )


def _definition(server: MCPHttp | None = None) -> PluginMCPServerDefinition:
    return PluginMCPServerDefinition(
        plugin_name="figma",
        plugin_namespace="figma",
        source_id="figma",
        private_alias=_ALIAS,
        server=server or _server(),
        config_file=Path("/plugins/figma/.mcp.json"),
    )


def _rival_definition() -> PluginMCPServerDefinition:
    # A second plugin that named its server ``figma`` too.
    return PluginMCPServerDefinition(
        plugin_name="design-tools",
        plugin_namespace="design_tools",
        source_id="figma",
        private_alias=_RIVAL_ALIAS,
        server=MCPHttp(
            name=_RIVAL_ALIAS,
            transport="http",
            url="https://figma.internal/mcp",
            auth=MCPStaticAuth(headers={"X-Internal-Bundle": "design_tools@1"}),
        ),
        config_file=Path("/plugins/design-tools/.mcp.json"),
    )


def _unauthorized() -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://mcp.figma.test/mcp")
    return httpx.HTTPStatusError(
        "401 Unauthorized",
        request=request,
        response=httpx.Response(401, request=request),
    )


def _catalog(
    service: MCPAuthenticationService | None = None,
) -> tuple[PluginMCPCatalog, MCPAuthenticationService]:
    authentication = service or MCPAuthenticationService()
    return PluginMCPCatalog(MCPRegistry(), authentication), authentication


def _unauthenticated(server: MCPHttp) -> Any:
    storage = AsyncMock()
    storage.get_tokens.return_value = None
    storage.token_expiry_time = None
    return (
        patch.object(
            Fingerprint, "load", new=AsyncMock(return_value=Fingerprint.compute(server))
        ),
        patch("vibe.app_server._mcp_auth.KeyringTokenStorage", return_value=storage),
    )


class _FakeRoot:
    def __init__(
        self, orchestrator: FakeConfigOrchestrator[Any], plugin_mcp: PluginMCPCatalog
    ) -> None:
        self.session_id = "session-1"
        self._orchestrator = orchestrator
        self._plugin_mcp = plugin_mcp
        self._state = SessionMCPState(
            catalog_revision="r1", route_revision="r1", sources=(), discovery_errors={}
        )
        self._runtime = build_unified_runtime_snapshot(
            cast(Any, orchestrator), AgentManager(cast(Any, orchestrator))
        )
        self.notified: list[str] = []
        self.suspended: list[str] = []
        self.reconfigured = 0

    @property
    def mcp_config_orchestrator(self) -> Any:
        return self._orchestrator

    @property
    def plugin_mcp_catalog(self) -> PluginMCPCatalog:
        return self._plugin_mcp

    async def read_mcp(self) -> SessionMCPState:
        return self._state

    async def reconfigure_mcp(
        self, configuration: ResolvedMCPCatalog, *, force_remote_discovery: bool
    ) -> SessionMCPState:
        self.reconfigured += 1
        return self._state

    async def authorization_changed(
        self, *, name: str, descriptor_revision: str
    ) -> SessionMCPState:
        self.notified.append(name)
        return self._state

    async def suspend_mcp(
        self, *, name: str, tool_name: str | None, reason: str
    ) -> SessionMCPState:
        self.suspended.append(name)
        return self._state

    def runtime_updated_params(self) -> RuntimeUpdatedParams:
        return RuntimeUpdatedParams(session_id=self.session_id, runtime=self._runtime)

    def update_mcp_projection(self, state: MCPState) -> None:
        self._runtime = self._runtime.model_copy(update={"mcp": state})


def _root(
    plugin_mcp: PluginMCPCatalog, *, configured: Sequence[MCPServer] = ()
) -> _FakeRoot:
    return _FakeRoot(
        FakeConfigOrchestrator(build_test_vibe_config(mcp_servers=list(configured))),
        plugin_mcp,
    )


async def _dispatch(
    service: MCPCatalogService, method: str, params: ProtocolModel, root: _FakeRoot
) -> Any:
    async def notify(_method: str, _params: ProtocolModel) -> None:
        return None

    result = await service.dispatch(
        method,
        params.model_dump(mode="json", by_alias=True),
        root=cast(Any, root),
        notify=notify,
    )
    return result.response


@pytest.mark.asyncio
async def test_the_catalog_knows_a_plugin_server_by_both_of_its_names() -> None:
    # Prepare
    catalog, _ = _catalog()

    # Do
    await catalog.bind([_definition()])

    # Assert
    assert catalog.aliases() == {"figma": _ALIAS}
    assert catalog.names() == {_ALIAS: "figma"}
    entry = catalog.entry("figma")
    assert entry is not None
    assert entry.server.name == "figma"
    assert entry.definition.server.name == _ALIAS


@pytest.mark.asyncio
async def test_a_declared_server_is_listed_before_anything_has_connected_it() -> None:
    # Prepare
    catalog, _ = _catalog()
    await catalog.bind([_definition()])

    # Do
    sources = catalog.sources()

    # Assert
    assert [(source.name, source.status) for source in sources] == [
        ("figma", "unavailable")
    ]


@pytest.mark.asyncio
async def test_discovery_resolves_a_plugin_server_through_the_app_owned_auth() -> None:
    # Prepare
    catalog, authentication = _catalog()
    definition = _definition()
    await catalog.bind([definition])
    seen: list[MCPAuthorizationRef] = []
    resolve = authentication.resolve

    async def spy(reference: MCPAuthorizationRef) -> Any:
        seen.append(reference)
        return await resolve(reference)

    authentication.resolve = spy  # type: ignore[method-assign]
    fingerprint_load, keyring = _unauthenticated(_server())

    # Do
    with fingerprint_load, keyring, pytest.raises(PluginMCPAuthorizationRequired):
        await catalog.discovery().discover(definition)

    # Assert
    assert [(ref.server_name, ref.kind) for ref in seen] == [("figma", "oauth")]
    assert [(source.name, source.status) for source in catalog.sources()] == [
        ("figma", "needs_auth")
    ]


@pytest.mark.asyncio
async def test_a_server_a_later_resolve_dropped_leaves_no_stale_status() -> None:
    # Prepare
    catalog, _ = _catalog()
    await catalog.bind([_definition()])
    catalog.record("figma", status="needs_auth")

    # Do
    await catalog.bind([])

    # Assert
    assert catalog.sources() == ()
    assert catalog.entry("figma") is None


@pytest.mark.asyncio
async def test_another_sessions_catalog_cannot_unbind_this_ones_servers() -> None:
    # Prepare
    catalog, authentication = _catalog()
    definition = _definition()
    await catalog.bind([definition])

    # Do
    # A second session opening in the same process whose plugins declare no MCP
    # servers. One authentication service is behind both catalogs.
    other, _ = _catalog(authentication)
    await other.bind([])

    # Assert
    fingerprint_load, keyring = _unauthenticated(_server())
    with fingerprint_load, keyring, pytest.raises(PluginMCPAuthorizationRequired):
        await catalog.discovery().discover(definition)
    assert [(source.name, source.status) for source in catalog.sources()] == [
        ("figma", "needs_auth")
    ]


@pytest.mark.asyncio
async def test_two_plugins_that_declared_one_name_both_keep_a_row() -> None:
    # Prepare
    catalog, _ = _catalog()

    # Do
    await catalog.bind([_definition(), _rival_definition()])

    # Assert
    assert catalog.aliases() == {_RIVAL_CONTESTED: _RIVAL_ALIAS, _CONTESTED: _ALIAS}
    assert [(source.name, source.plugin_name) for source in catalog.sources()] == [
        (_RIVAL_CONTESTED, "design-tools"),
        (_CONTESTED, "figma"),
    ]
    # Neither side keeps the bare id, so installing the second plugin cannot
    # leave the first answering under a name the other also answers to.
    assert catalog.entry("figma") is None


@pytest.mark.asyncio
async def test_a_contested_name_records_each_status_against_its_own_plugin() -> None:
    # Prepare
    catalog, _ = _catalog()
    rival = _rival_definition()
    await catalog.bind([_definition(_declared_server()), rival])
    storage = AsyncMock()
    storage.get_tokens.return_value = None
    storage.token_expiry_time = None

    # Do
    with (
        patch.object(Fingerprint, "load", new=AsyncMock(return_value=None)),
        patch("vibe.app_server._mcp_auth.KeyringTokenStorage", return_value=storage),
        patch(
            "vibe.core.tools.mcp.registry.list_tools_http",
            new=AsyncMock(return_value=[RemoteTool(name="get_internal_context")]),
        ),
    ):
        await catalog.discovery().discover(rival)

    # Assert
    assert [
        (source.name, source.status, [tool.name for tool in source.tools])
        for source in catalog.sources()
    ] == [
        (_RIVAL_CONTESTED, "connected", ["get_internal_context"]),
        (_CONTESTED, "unavailable", []),
    ]


@pytest.mark.asyncio
async def test_a_plugin_server_is_read_as_a_server_that_names_its_owner() -> None:
    # Prepare
    from vibe.app_server._plugin_mcp import PluginMCPTool

    catalog, authentication = _catalog()
    await catalog.bind([_definition()])
    catalog.record(
        "figma", status="connected", tools=[PluginMCPTool(name="get_design_context")]
    )
    service = MCPCatalogService(authentication)
    root = _root(catalog)

    # Do
    response = cast(
        MCPReadResponse,
        await _dispatch(
            service, "mcp_catalog/read", MCPReadParams(session_id="session-1"), root
        ),
    )

    # Assert
    assert [
        (source.name, source.kind, source.status, source.plugin_name)
        for source in response.mcp.sources
    ] == [("figma", MCPSourceKind.SERVER, MCPSourceStatus.CONNECTED, "figma")]
    assert [tool.name for tool in response.mcp.sources[0].tools] == [
        "get_design_context"
    ]
    assert response.mcp.needs_auth == []


@pytest.mark.asyncio
async def test_a_plugin_server_that_failed_reports_its_reason_as_a_discovery_error() -> (
    None
):
    # Prepare
    catalog, authentication = _catalog()
    await catalog.bind([_definition()])
    catalog.record("figma", status="unavailable", error="connection refused")
    root = _root(catalog)

    # Do
    response = cast(
        MCPReadResponse,
        await _dispatch(
            MCPCatalogService(authentication),
            "mcp_catalog/read",
            MCPReadParams(session_id="session-1"),
            root,
        ),
    )

    # Assert
    assert response.mcp.discovery_errors == {"figma": "connection refused"}
    assert response.mcp.sources[0].status is MCPSourceStatus.UNAVAILABLE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "params"),
    [
        (
            "mcp_catalog/toggle",
            MCPToggleParams(
                session_id="session-1", name="figma", source="server", disabled=True
            ),
        ),
        ("mcp_catalog/remove", MCPRemoveParams(session_id="session-1", name="figma")),
    ],
)
async def test_a_plugin_server_is_not_the_catalogs_to_toggle_or_remove(
    method: str, params: ProtocolModel
) -> None:
    # Prepare
    catalog, authentication = _catalog()
    await catalog.bind([_definition()])
    root = _root(catalog)

    # Do
    with pytest.raises(RequestFailure) as failure:
        await _dispatch(MCPCatalogService(authentication), method, params, root)

    # Assert
    assert failure.value.code is ProtocolErrorCode.INVALID_PARAMS
    assert str(failure.value) == (
        "MCP server 'figma' is managed by the 'figma' plugin and cannot be "
        "toggled or removed from the MCP catalog."
    )
    assert root.suspended == []


@pytest.mark.asyncio
async def test_a_configured_server_that_took_the_name_is_the_catalogs_to_toggle() -> (
    None
):
    # Prepare
    catalog, authentication = _catalog()
    await catalog.bind([_definition()])
    root = _root(catalog, configured=[_configured_server()])

    # Do
    with patch(
        "vibe.app_server.mcp_catalog.persist_mcp_toggle", new=AsyncMock()
    ) as persist:
        await _dispatch(
            MCPCatalogService(authentication),
            "mcp_catalog/toggle",
            MCPToggleParams(
                session_id="session-1", name="figma", source="server", disabled=True
            ),
            root,
        )

    # Assert
    persist.assert_awaited_once()
    assert persist.await_args is not None
    assert persist.await_args.kwargs["name"] == "figma"
    assert root.suspended == ["figma"]


@pytest.mark.asyncio
async def test_a_configured_server_that_took_the_name_owns_the_login() -> None:
    # Prepare
    catalog, authentication = _catalog()
    await catalog.bind([_definition()])
    root = _root(catalog, configured=[_configured_server()])

    # Do
    with patch(
        "vibe.app_server._mcp_auth.perform_oauth_login", new=AsyncMock()
    ) as login:
        response = cast(
            MCPCatalogMutationResponse,
            await _dispatch(
                MCPCatalogService(authentication),
                "mcp_catalog/login",
                MCPLoginParams(session_id="session-1", name="figma"),
                root,
            ),
        )

    # Assert
    assert login.await_args is not None
    assert login.await_args.args[0].url == "https://figma.example/mcp"
    assert root.notified == ["figma"]
    assert response.runtime is not None
    assert [
        (source.name, source.plugin_name) for source in response.runtime.mcp.sources
    ] == [("figma", None)]


@pytest.mark.asyncio
async def test_refreshing_the_catalog_retries_a_plugin_server_that_failed() -> None:
    # Prepare
    catalog, authentication = _catalog()
    definition = _definition(_declared_server())
    await catalog.bind([definition])
    catalog.record("figma", status="unavailable", error="connection refused")
    root = _root(catalog)
    storage = AsyncMock()
    storage.get_tokens.return_value = None
    storage.token_expiry_time = None

    # Do
    with (
        patch.object(Fingerprint, "load", new=AsyncMock(return_value=None)),
        patch("vibe.app_server._mcp_auth.KeyringTokenStorage", return_value=storage),
        patch(
            "vibe.core.tools.mcp.registry.list_tools_http",
            new=AsyncMock(return_value=[RemoteTool(name="get_design_context")]),
        ),
    ):
        response = cast(
            MCPCatalogMutationResponse,
            await _dispatch(
                MCPCatalogService(authentication),
                "mcp_catalog/refresh",
                MCPRefreshParams(session_id="session-1"),
                root,
            ),
        )

    # Assert
    assert response.runtime is not None
    assert [
        (source.name, source.status) for source in response.runtime.mcp.sources
    ] == [("figma", MCPSourceStatus.CONNECTED)]
    assert response.runtime.mcp.discovery_errors == {}


@pytest.mark.asyncio
async def test_logging_in_to_a_plugin_server_reconnects_it_without_the_session() -> (
    None
):
    # Prepare
    catalog, authentication = _catalog()
    definition = _definition()
    await catalog.bind([definition])
    catalog.record("figma", status="unavailable", error="never tried")
    root = _root(catalog)
    fingerprint_load, keyring = _unauthenticated(_server())

    # Do
    with (
        fingerprint_load,
        keyring,
        patch(
            "vibe.app_server._mcp_auth.perform_oauth_login", new=AsyncMock()
        ) as login,
    ):
        response = cast(
            MCPCatalogMutationResponse,
            await _dispatch(
                MCPCatalogService(authentication),
                "mcp_catalog/login",
                MCPLoginParams(session_id="session-1", name="figma"),
                root,
            ),
        )

    # Assert
    assert login.await_args is not None
    assert login.await_args.args[0].name == "figma"
    assert root.notified == []
    assert root.suspended == []
    assert response.runtime is not None
    assert [
        (source.name, source.status, source.plugin_name)
        for source in response.runtime.mcp.sources
    ] == [("figma", MCPSourceStatus.NEEDS_AUTH, "figma")]


@pytest.mark.asyncio
async def test_logging_out_of_a_plugin_server_suspends_no_session_routes() -> None:
    # Prepare
    catalog, authentication = _catalog()
    definition = _definition()
    await catalog.bind([definition])
    root = _root(catalog)
    fingerprint_load, keyring = _unauthenticated(_server())

    # Do
    with (
        fingerprint_load,
        keyring,
        patch(
            "vibe.app_server._mcp_auth.delete_oauth_credentials", new=AsyncMock()
        ) as delete,
    ):
        response = cast(
            MCPCatalogMutationResponse,
            await _dispatch(
                MCPCatalogService(authentication),
                "mcp_catalog/logout",
                MCPLogoutParams(session_id="session-1", name="figma"),
                root,
            ),
        )

    # Assert
    delete.assert_awaited_once_with("figma")
    assert root.suspended == []
    assert root.reconfigured == 0
    assert response.runtime is not None
    assert [source.name for source in response.runtime.mcp.sources] == ["figma"]


@pytest.mark.asyncio
async def test_a_statically_declared_plugin_server_reaches_a_login_through_its_401() -> (
    None
):
    # Prepare
    catalog, authentication = _catalog()
    definition = _definition(_declared_server())
    await catalog.bind([definition])
    storage = AsyncMock()
    storage.get_tokens.return_value = None
    storage.token_expiry_time = None
    list_tools = AsyncMock(side_effect=_unauthorized())

    # Do
    with (
        patch.object(Fingerprint, "load", new=AsyncMock(return_value=None)),
        patch("vibe.app_server._mcp_auth.KeyringTokenStorage", return_value=storage),
        patch("vibe.core.tools.mcp.registry.list_tools_http", new=list_tools),
    ):
        with pytest.raises(PluginMCPAuthorizationRequired):
            await catalog.discovery().discover(definition)
        with patch(
            "vibe.app_server._mcp_auth.perform_oauth_login", new=AsyncMock()
        ) as login:
            await authentication.login("figma", on_url=AsyncMock())

    # Assert
    list_tools.assert_awaited_once()
    assert list_tools.await_args is not None
    assert list_tools.await_args.kwargs["headers"] == {
        "X-Figma-Plugin-Bundle": "figma_prod@2_2_96"
    }
    assert [(source.name, source.status) for source in catalog.sources()] == [
        ("figma", "needs_auth")
    ]
    assert login.await_args is not None
    assert login.await_args.args[0].auth == MCPOAuth(type="oauth", scopes=[])
