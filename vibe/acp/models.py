from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from vibe.app_server.models import (
    MCPSourceKind,
    MCPSourceStatus,
    MCPSourceSummary,
    MCPState,
)


class ConfigSchemaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    version: str
    config_schema: dict[str, Any] = Field(alias="schema")


# -- Project links ------------------------------------------------------------
#
# Request params for the projectLinks/* ACP ext methods. Every method is
# stateless and takes the absolute `rootPath` held by desktop-main; the
# app-server ProjectLinksController intentionally returns `repoLocalPath`;
# renderers that need compact labels should derive them from the basename.


class ProjectLinksListRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class ProjectLinksResolveRootRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    root_path: str = Field(alias="rootPath", min_length=1)


class ProjectLinksPickerLoadRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    root_path: str = Field(alias="rootPath", min_length=1)


class ProjectLinksPickerLoadMoreRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    root_path: str = Field(alias="rootPath", min_length=1)
    cursor: str = Field(min_length=1)


class ProjectLinksCreateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    root_path: str = Field(alias="rootPath", min_length=1)
    name: str = Field(min_length=1)
    default_branch: str = Field(alias="defaultBranch", min_length=1)


class ProjectLinksLinkRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    root_path: str = Field(alias="rootPath", min_length=1)
    project_id: str = Field(alias="projectId", min_length=1)
    project_name: str = Field(alias="projectName", min_length=1)


class ProjectLinksUnlinkRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    root_path: str = Field(alias="rootPath", min_length=1)


# -- Connectors ---------------------------------------------------------------
# Declared here, not reused from the app-server's internal MCPSourceSummary.


class ConnectorReachability(StrEnum):
    CONNECTED = "connected"
    NEEDS_AUTH = "needs_auth"
    NEEDS_SETUP = "needs_setup"
    UNAVAILABLE = "unavailable"
    # A disabled connector is never probed.
    UNKNOWN = "unknown"


# The one place the app-server's flattened status is split into the two axes.
_CONNECTOR_INTENT: dict[MCPSourceStatus, tuple[bool, ConnectorReachability]] = {
    MCPSourceStatus.CONNECTED: (True, ConnectorReachability.CONNECTED),
    MCPSourceStatus.ENABLED: (True, ConnectorReachability.CONNECTED),
    MCPSourceStatus.NEEDS_AUTH: (True, ConnectorReachability.NEEDS_AUTH),
    MCPSourceStatus.NEEDS_SETUP: (True, ConnectorReachability.NEEDS_SETUP),
    MCPSourceStatus.UNAVAILABLE: (True, ConnectorReachability.UNAVAILABLE),
    MCPSourceStatus.DISABLED: (False, ConnectorReachability.UNKNOWN),
}


class ConnectorToolResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    enabled: bool


class ConnectorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    enabled: bool
    reachability: ConnectorReachability
    tools: list[ConnectorToolResponse] = Field(default_factory=list)
    error: str | None = None

    @classmethod
    def from_source(cls, source: MCPSourceSummary) -> ConnectorResponse:
        enabled, reachability = _CONNECTOR_INTENT[source.status]
        return cls(
            name=source.name,
            enabled=enabled,
            reachability=reachability,
            tools=[
                ConnectorToolResponse(
                    name=tool.name, description=tool.description, enabled=tool.enabled
                )
                for tool in source.tools
            ],
            error=source.error,
        )


class ConnectorsListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connectors: list[ConnectorResponse]
    # Bootstrap-level failure, as opposed to one connector's own error.
    error: str | None = None

    @classmethod
    def from_state(cls, state: MCPState) -> ConnectorsListResponse:
        # Local MCP servers ride the same payload and are not connectors.
        return cls(
            connectors=[
                ConnectorResponse.from_source(source)
                for source in state.sources
                if source.kind is MCPSourceKind.CONNECTOR
            ],
            error=state.connector_error,
        )


class ConnectorAuthUrlResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Null when the connector needs admin setup, so there is nothing to open.
    url: str | None = None


class ConnectorsListRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    session_id: str = Field(alias="sessionId", min_length=1)


class ConnectorRequest(BaseModel):
    model_config = ConfigDict(
        extra="ignore", populate_by_name=True, str_strip_whitespace=True
    )

    session_id: str = Field(alias="sessionId", min_length=1)
    name: str = Field(min_length=1)


class ConnectorsRefreshRequest(BaseModel):
    model_config = ConfigDict(
        extra="ignore", populate_by_name=True, str_strip_whitespace=True
    )

    session_id: str = Field(alias="sessionId", min_length=1)
    names: list[Annotated[str, StringConstraints(min_length=1)]] = Field(min_length=1)


class ConnectorsToggleRequest(BaseModel):
    model_config = ConfigDict(
        extra="ignore", populate_by_name=True, str_strip_whitespace=True
    )

    session_id: str = Field(alias="sessionId", min_length=1)
    name: str = Field(min_length=1)
    disabled: bool
    tool_name: str | None = Field(alias="toolName", default=None, min_length=1)
