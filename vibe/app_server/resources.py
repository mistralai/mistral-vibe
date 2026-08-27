from __future__ import annotations

from vibe.app_server._integration_resources import (
    MCPResource,
    PluginCatalogChange,
    PluginCatalogDiff,
    PluginCatalogResource,
    VibeCodeResource,
)
from vibe.app_server._runtime_resources import (
    AccountResource,
    AgentResource,
    ConfigResource,
    IdentityResource,
    RuntimeResource,
)
from vibe.app_server._service_resources import (
    FeedbackResource,
    NarrationResource,
    TelemetryResource,
)
from vibe.app_server._session_resources import (
    LoopsResource,
    ReviewResource,
    SessionResource,
    ShellResource,
    WorkspaceResource,
)
from vibe.app_server.client_state import ClientSessionState
from vibe.app_server.connection import AppServerResourceConnection
from vibe.app_server.events import AppServerEvent
from vibe.app_server.protocol import Notification

__all__ = ["AppServerResources", "PluginCatalogChange", "PluginCatalogDiff"]


class AppServerResources:
    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self.account = AccountResource(connection, state)
        self.identity = IdentityResource(connection, state)
        self.config = ConfigResource(connection, state)
        self.agents = AgentResource(connection, state)
        self.runtime = RuntimeResource(connection, state)
        self.mcp = MCPResource(connection, state)
        self.plugins = PluginCatalogResource(connection, state)
        self.shell = ShellResource(connection, state)
        self.sessions = SessionResource(connection, state)
        self.review = ReviewResource(connection, state)
        self.workspace = WorkspaceResource(connection, state)
        self.loops = LoopsResource(connection, state)
        self.telemetry = TelemetryResource(connection, state)
        self.narration = NarrationResource(connection, state)
        self.feedback = FeedbackResource(connection, state)
        self.vibe_code = VibeCodeResource(connection, state)

    async def refresh(self) -> None:
        await self.runtime.refresh()

    async def consume_notification(self, notification: Notification) -> bool:
        previous_config = self.config.current
        if await self.runtime.consume_notification(notification):
            self.config.publish_change(previous_config)
            return True
        if await self.mcp.consume_notification(notification):
            return True
        return await self.vibe_code.consume_notification(notification)

    async def consume_event(self, event: AppServerEvent) -> bool:
        return await self.shell.consume_event(event)
