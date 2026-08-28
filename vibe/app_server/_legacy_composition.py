from __future__ import annotations

from vibe.app_server._account import AccountGateway
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._identity import IdentityGateway
from vibe.app_server._legacy_session_runtime import (
    OpenRoot,
    StageRoot,
    create_legacy_session_backend_host,
)
from vibe.app_server._mcp_auth import MCPAuthenticationService
from vibe.app_server._runtime import AgentRuntimeFactory
from vibe.app_server._session_backend_port import SessionBackendHost
from vibe.app_server._session_backend_services import SessionBackendServices
from vibe.app_server.connector_catalog import ConnectorCatalogService
from vibe.app_server.mcp_catalog import MCPCatalogService
from vibe.app_server.protocol import TransportKind
from vibe.app_server.server import AppServer
from vibe.app_server.transport import JsonRpcTransport
from vibe.core.config.harness_files import HarnessFilesManager


def create_legacy_app_server(
    transport: JsonRpcTransport,
    *,
    open_root: OpenRoot,
    transport_kind: TransportKind = "in_process",
    account_gateway: AccountGateway | None = None,
    identity_gateway: IdentityGateway | None = None,
    runtime_factory: AgentRuntimeFactory | None = None,
    host_handler: HostRequestHandler | None = None,
    stage_root: StageRoot | None = None,
    mcp_catalog_service: MCPCatalogService | None = None,
    connector_catalog_service: ConnectorCatalogService | None = None,
) -> AppServer:
    effective_host_handler = host_handler or HostRequestHandler(
        HarnessFilesManager(sources=("user", "project"))
    )
    runtime_factory = runtime_factory or AgentRuntimeFactory()
    effective_mcp_catalog = mcp_catalog_service or MCPCatalogService(
        MCPAuthenticationService()
    )
    effective_connector_catalog = connector_catalog_service or ConnectorCatalogService(
        implicit_source_enabled=False
    )

    def create_session_backend_host(
        services: SessionBackendServices,
    ) -> SessionBackendHost:
        return create_legacy_session_backend_host(
            open_root=open_root,
            runtime_factory=runtime_factory,
            host_handler=effective_host_handler,
            stage_root=stage_root,
            services=services,
            mcp_catalog_service=effective_mcp_catalog,
            connector_catalog_service=effective_connector_catalog,
            account_gateway=account_gateway,
            identity_gateway=identity_gateway,
        )

    return AppServer(
        transport,
        session_backend_host_factory=create_session_backend_host,
        transport_kind=transport_kind,
        account_gateway=account_gateway,
        identity_gateway=identity_gateway,
        host_handler=effective_host_handler,
        mcp_catalog_service=effective_mcp_catalog,
        connector_catalog_service=effective_connector_catalog,
    )
