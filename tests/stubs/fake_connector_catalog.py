from __future__ import annotations

from tests.stubs.fake_connector_registry import FakeConnectorRegistry
from vibe.app_server._session_backend_port import (
    ResolvedConnector,
    ResolvedConnectorCatalog,
    ResolvedConnectorTool,
    SessionBackend,
)
from vibe.app_server.connector_catalog import (
    ConnectorCatalogReadResult,
    ConnectorCatalogService,
    ConnectorCatalogUnavailableError,
)
from vibe.app_server.protocol import ConnectorAuthReadParams, ConnectorAuthReadResponse
from vibe.core.config import VibeConfigSchema
from vibe.core.config.orchestrator import ConfigOrchestrator


class FakeConnectorCatalogService(ConnectorCatalogService):
    """Test catalog owner backed by the same fixture as the legacy executor."""

    def __init__(self, registry: FakeConnectorRegistry) -> None:
        super().__init__(implicit_source_enabled=False)
        self._registry = registry
        self._catalog = ResolvedConnectorCatalog(
            provider_fingerprint="fake-provider",
            revision="fake-catalog-v1",
            connectors=tuple(
                ResolvedConnector(
                    raw_id=entry.connector_id,
                    alias=entry.alias,
                    display_name=entry.display_name,
                    ready=entry.ready,
                    auth_action=entry.auth_action.value,
                    tools=tuple(
                        ResolvedConnectorTool(
                            raw_name=tool.name,
                            description=tool.description,
                            input_schema=tool.input_schema,
                        )
                        for tool in entry.tools
                    ),
                    diagnostics=(entry.diagnostic,) if entry.diagnostic else (),
                )
                for entry in registry.catalog_entries()
            ),
        )

    def read_catalog(
        self, orchestrator: ConfigOrchestrator[VibeConfigSchema]
    ) -> ConnectorCatalogReadResult:
        del orchestrator
        if self._registry.bootstrap_error() is not None:
            return ConnectorCatalogReadResult(catalog=None, disposition="not_loaded")
        return ConnectorCatalogReadResult(catalog=self._catalog, disposition="memory")

    async def resolve_catalog(
        self,
        orchestrator: ConfigOrchestrator[VibeConfigSchema],
        *,
        force_refresh: bool = False,
    ) -> ResolvedConnectorCatalog:
        del orchestrator, force_refresh
        if error := self._registry.bootstrap_error():
            raise ConnectorCatalogUnavailableError(error)
        return self._catalog

    async def _compat_auth_read(
        self, params: ConnectorAuthReadParams, root: SessionBackend | None
    ) -> ConnectorAuthReadResponse:
        context = await self._target(params.session_id, root)
        await context.require_control().request_connector_auth(alias=params.name)
        return ConnectorAuthReadResponse(
            url=await self._registry.get_auth_url(params.name)
        )
