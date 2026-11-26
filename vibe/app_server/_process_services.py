"""The services one OS process shares across all its channels and sessions.

A multiplexed app server opens one channel per client connection, and each
channel builds its own ``HarnessProcess``. What must be shared by all of them
(a remote value refreshed once for everyone, say) lives here, built once by
the entry point and handed to every ``HarnessProcess``. Never in a module
global: tests then get fresh services each, and a service's lifetime is the
process's, visible at the one place that creates and closes it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from vibe.app_server._background_worker import BackgroundWorker
from vibe.app_server._config_changes import ConfigChanges
from vibe.app_server._experiments_service import ExperimentsService
from vibe.app_server._keep_awake import KeepAwake
from vibe.app_server._left_turns import LeftTurns
from vibe.app_server._mcp_discovery_service import MCPDiscoveryService
from vibe.app_server._missing_model_warnings import MissingModelWarnings
from vibe.app_server._model_catalog_service import ModelCatalogService
from vibe.app_server._open_sessions import OpenSessions
from vibe.app_server._org_config_service import OrgConfigService
from vibe.app_server._platform_credential_lenders import PlatformCredentialLenders
from vibe.app_server._skill_shadows import ShadowWarnings
from vibe.app_server._skill_store_cleanup import SkillStoreCleanup
from vibe.app_server._storage_usage import StorageUsageService
from vibe.app_server._vibe_bot import VIBE_BOT_CLIENT_ID_FILE, VibeBotLocalSessions
from vibe.app_server._worktree_housekeeping import WorktreeHousekeeping
from vibe.app_server.connector_catalog import SharedConnectorCatalog
from vibe.core.paths import VIBE_HOME
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
    missing_models: MissingModelWarnings
    skill_store: SkillStoreCleanup
    # Every channel's open sessions, so one channel archives another's.
    open_sessions: OpenSessions
    storage_usage: StorageUsageService
    model_catalog: ModelCatalogService
    # Tells subscribed connections that the config a new session would run may
    # have changed: the org config or the model catalog refreshed, or a
    # session-less setting written.
    config_changes: ConfigChanges
    # The one request that keeps the machine awake while any session runs a turn.
    keep_awake: KeepAwake
    # The bot's local sessions this process runs: a reply owed to the bot
    # reaches it from any connection.
    vibe_bot_sessions: VibeBotLocalSessions
    # Desktop channels by account, so a session whose channel closed borrows
    # its account's sign-in from another.
    platform_credential_lenders: PlatformCredentialLenders
    # Turns an earlier process left mid-flight, resumed once per owner.
    left_turns: LeftTurns

    def __post_init__(self) -> None:
        self.org_config.on_changed(lambda _key: self.config_changes.publish())
        self.model_catalog.on_changed(lambda _key: self.config_changes.publish())

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
            missing_models=MissingModelWarnings(),
            skill_store=SkillStoreCleanup(background),
            open_sessions=OpenSessions(),
            storage_usage=StorageUsageService(background),
            model_catalog=ModelCatalogService(),
            config_changes=ConfigChanges(),
            keep_awake=KeepAwake(),
            vibe_bot_sessions=VibeBotLocalSessions(
                client_id_file=VIBE_HOME.path / VIBE_BOT_CLIENT_ID_FILE
            ),
            platform_credential_lenders=PlatformCredentialLenders(),
            left_turns=LeftTurns(),
        )

    async def close(self) -> None:
        # Off the loop: stopping the request waits for its process to exit.
        await asyncio.to_thread(self.keep_awake.close)
        try:
            await self.left_turns.close()
            await self.org_config.close()
            await self.connector_catalog.close()
            await self.experiments.close()
            await self.mcp_discovery.close()
            await self.storage_usage.close()
            await self.model_catalog.close()
        finally:
            await self.background.close()


__all__ = ["ProcessServices"]
