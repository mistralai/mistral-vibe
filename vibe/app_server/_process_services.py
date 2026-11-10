"""The services one OS process shares across all its channels and sessions.

A multiplexed app server opens one channel per client connection, and each
channel builds its own ``HarnessProcess``. What must be shared by all of them
(a remote value refreshed once for everyone, say) lives here, built once by
the entry point and handed to every ``HarnessProcess``. Never in a module
global: tests then get fresh services each, and a service's lifetime is the
process's, visible at the one place that creates and closes it.
"""

from __future__ import annotations

from dataclasses import dataclass

from vibe.app_server._background_worker import BackgroundWorker
from vibe.app_server._experiments_service import ExperimentsService
from vibe.app_server._mcp_discovery_service import MCPDiscoveryService
from vibe.app_server._model_catalog_service import ModelCatalogService
from vibe.app_server._open_sessions import OpenSessions
from vibe.app_server._org_config_service import OrgConfigService
from vibe.app_server._skill_shadows import ShadowWarnings
from vibe.app_server._skill_store_cleanup import SkillStoreCleanup
from vibe.app_server._worktree_housekeeping import WorktreeHousekeeping
from vibe.app_server.connector_catalog import SharedConnectorCatalog
from vibe.setup.auth._oidc_tenant import OidcTenantResolver


@dataclass(frozen=True, slots=True)
class ProcessServices:
    org_config: OrgConfigService
    connector_catalog: SharedConnectorCatalog
    # The OIDC tenant per signed-in user, shared by CLI and Desktop connections.
    oidc_tenants: OidcTenantResolver
    background: BackgroundWorker
    worktrees: WorktreeHousekeeping
    experiments: ExperimentsService
    mcp_discovery: MCPDiscoveryService
    skill_shadows: ShadowWarnings
    skill_store: SkillStoreCleanup
    # Every channel's open sessions, so one channel archives another's.
    open_sessions: OpenSessions
    model_catalog: ModelCatalogService

    @classmethod
    def create(cls) -> ProcessServices:
        """The production services; an entry point calls this once."""
        background = BackgroundWorker()
        return cls(
            org_config=OrgConfigService(),
            connector_catalog=SharedConnectorCatalog(),
            oidc_tenants=OidcTenantResolver(),
            background=background,
            worktrees=WorktreeHousekeeping(background),
            experiments=ExperimentsService(),
            mcp_discovery=MCPDiscoveryService(),
            skill_shadows=ShadowWarnings(),
            skill_store=SkillStoreCleanup(background),
            open_sessions=OpenSessions(),
            model_catalog=ModelCatalogService(),
        )

    async def close(self) -> None:
        try:
            await self.org_config.close()
            await self.connector_catalog.close()
            await self.experiments.close()
            await self.mcp_discovery.close()
            await self.model_catalog.close()
        finally:
            await self.background.close()


__all__ = ["ProcessServices"]
